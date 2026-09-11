"""Text columns fetched when an item is looked at, not when the catalogue opens.

Text is the largest thing left in the process once the embeddings are memory
mapped: 3.1 KB per item across nine columns. Unlike embeddings it is variable
length, so it cannot be a matrix, and unlike embeddings almost none of it is
read on the hot path -- a search scores the few thousand candidates the vector
index returned, and serialises the fifty it keeps. The rest of the catalogue is
carried for nothing.

So the text stays in SQLite, where it already was, and comes back keyed by id.
Two things keep that from costing a query per candidate: the scorer asks for the
whole candidate set at once, in batches; and a bounded cache holds what was just
read, because scoring, filtering and serialising all touch the same rows within
one request. Bounded is the point -- an unbounded cache is the resident
catalogue again, reached by a slower path.

**Normalised once, not once per query.** Ranking never reads the raw string: it
asks whether a term appears, which means lowercase, accents folded, punctuation
dropped. Doing that inside the scoring loop made a search normalise the same
field several times for the same item, and then do it again on the next query --
88% of a query's time, measured. The normalised form is cached beside the raw
one, so it is computed once per item while that item is warm.

**Two groups, because they have different readers.** The eight columns ranking
uses are read for every candidate. ``visual_json`` is read for none of them: it
is serialised with the handful of results that survive, and it is also half the
bytes. Fetching it with the rest made a query move 9 MB to rank 3,000
candidates, of which 4.8 MB was never looked at.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Sequence

from core.search_types import normalize_text

# Order is the contract: IndexRecord reads these by position.
SCORING_COLUMNS: tuple[str, ...] = (
    "texto_extraido",
    "descricao_ia",
    "tags",
    "objects",
    "style",
    "source_work",
    "humor",
    "context",
)

DETAIL_COLUMN = "visual_json"

TEXT_COLUMNS: tuple[str, ...] = (*SCORING_COLUMNS, DETAIL_COLUMN)

EMPTY_TEXT: tuple[str, ...] = ("",) * len(SCORING_COLUMNS)

# SQLite binds at most 999 parameters by default, and one batch should not be so
# large that a single candidate set blows the cache it is about to fill.
_BATCH = 512

# A query legitimately needs every candidate warm at once, so the caches grow to
# fit one candidate set and no further. The ceiling is what keeps this a working
# set: without it a large enough pool would hold the catalogue again.
_MAX_CACHE = 16384


class _Group:
    """One set of columns, its cache, and the statement that fills it."""

    def __init__(self, db, columns: Sequence[str], present: Sequence[str], cache_size: int):
        self._db = db
        self._columns = tuple(present)
        self._positions = tuple(columns.index(name) for name in present)
        self._width = len(columns)
        self._empty = ("",) * self._width
        self._cache: OrderedDict[int, tuple[str, ...]] = OrderedDict()
        self._cache_size = max(cache_size, 1)

    @property
    def enabled(self) -> bool:
        return bool(self._columns)

    def get(self, db_id: int) -> tuple[str, ...]:
        cached = self._cache.get(db_id)
        if cached is not None:
            self._cache.move_to_end(db_id)
            return cached
        if not db_id or not self._columns:
            return self._empty
        self.read([db_id])
        return self._cache.get(db_id, self._empty)

    def warm(self, db_ids: Iterable[int]) -> None:
        if not self._columns:
            return
        missing = [
            db_id for db_id in dict.fromkeys(db_ids) if db_id and db_id not in self._cache
        ]
        for start in range(0, len(missing), _BATCH):
            self.read(missing[start : start + _BATCH])

    def read(self, db_ids: list[int]) -> None:
        if not db_ids:
            return
        placeholders = ",".join("?" * len(db_ids))
        sql = (
            f"SELECT id, {', '.join(self._columns)} FROM memes WHERE id IN ({placeholders})"  # noqa: S608
        )
        try:
            rows = self._db.get_connection().execute(sql, db_ids).fetchall()
        except Exception:
            # A catalogue that cannot answer degrades to empty text, which costs
            # lexical matches on those rows. Refusing the search instead would
            # turn a missing column into a blank gallery.
            rows = []
        found = set()
        for row in rows:
            values = list(self._empty)
            for offset, position in enumerate(self._positions, start=1):
                values[position] = row[offset] or ""
            db_id = int(row[0])
            found.add(db_id)
            self._remember(db_id, tuple(values))
        for db_id in db_ids:
            if db_id not in found:
                # Remembered as empty on purpose: a row that is gone must not be
                # asked for again on every candidate loop.
                self._remember(db_id, self._empty)

    def resize(self, cache_size: int) -> None:
        self._cache_size = max(cache_size, 1)
        _evict(self._cache, self._cache_size)

    def _remember(self, db_id: int, values: tuple[str, ...]) -> None:
        self._cache[db_id] = values
        self._cache.move_to_end(db_id)
        _evict(self._cache, self._cache_size)


class TextStore:
    """The text of a catalogue, read by id and remembered for a while."""

    def __init__(self, db, columns: Sequence[str], cache_size: int = 4096):
        available = set(columns)
        self._scoring = _Group(
            db,
            SCORING_COLUMNS,
            [name for name in SCORING_COLUMNS if name in available],
            cache_size,
        )
        # A far smaller cache: only results that reach the client are detailed,
        # and one page of them is fifty.
        # Bounded like the raw text, and for the same reason: this is a cache of
        # the catalogue, not a second copy of it.
        self._normalized: OrderedDict[int, tuple[tuple[str, ...], str]] = OrderedDict()
        self._floor = max(cache_size, 1)
        self._normalized_size = self._floor
        self._detail = _Group(
            db,
            (DETAIL_COLUMN,),
            [DETAIL_COLUMN] if DETAIL_COLUMN in available else [],
            256,
        )

    @property
    def enabled(self) -> bool:
        return self._scoring.enabled or self._detail.enabled

    def prefetch(self, db_ids: Iterable[int]) -> None:
        """Warm the ranking columns for a candidate set, in batches."""
        ids = list(db_ids)
        # Sized before filling. A cache smaller than the candidate set evicts the
        # rows it is about to be asked for, which turns every read into a miss --
        # measured as fourteen re-foldings per candidate.
        self.reserve(len(ids))
        self._scoring.warm(ids)

    def reserve(self, count: int) -> None:
        wanted = min(max(count, self._floor), _MAX_CACHE)
        self._scoring.resize(wanted)
        self._normalized_size = wanted

    def fetch(self, db_id: int) -> tuple[str, ...]:
        return self._scoring.get(db_id)

    def fetch_detail(self, db_id: int) -> str:
        return self._detail.get(db_id)[0]

    def fetch_normalized(self, db_id: int) -> tuple[tuple[str, ...], str]:
        """The ranking columns folded for matching, plus all of them joined."""
        cached = self._normalized.get(db_id)
        if cached is not None:
            self._normalized.move_to_end(db_id)
            return cached
        folded = normalize_fields(self.fetch(db_id))
        if db_id:
            self._normalized[db_id] = folded
            _evict(self._normalized, self._normalized_size)
        return folded


def _evict(cache: OrderedDict, limit: int) -> None:
    """Drop the oldest entries down to ``limit``.

    The server answers requests on a thread pool, so two threads can find the
    cache over its limit at the same time and both pop. Dropping one entry too
    many is harmless; raising on an empty cache is not, and that is the only way
    this can fail.
    """
    while len(cache) > limit:
        try:
            cache.popitem(last=False)
        except KeyError:  # pragma: no cover - another thread got there first
            return


def normalize_fields(values: Sequence[str]) -> tuple[tuple[str, ...], str]:
    """Fold each field for matching and join them.

    Joining the folded fields is the same text as folding the joined fields:
    normalisation is per character and the fields are already separated by a
    space. Doing it this way means the join costs nothing extra.
    """
    folded = tuple(normalize_text(value) for value in values)
    return folded, " ".join(field for field in folded if field)
