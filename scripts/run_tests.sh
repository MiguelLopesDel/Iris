#!/usr/bin/env bash
# Test runner do Iris.
# Roda a suíte padrão, grupos especializados (atrás de portões de ambiente) ou todos.
#
#   scripts/run_tests.sh                 # suíte padrão (rápida, sem modelos)
#   scripts/run_tests.sh standard        # idem
#   scripts/run_tests.sh db              # API contra DB real        (TEST_DB)
#   scripts/run_tests.sh model           # testes que carregam CLIP  (IRIS_RUN_MODEL_TESTS=1)
#   scripts/run_tests.sh integration     # pipeline de dedup E2E     (IRIS_INTEGRATION=1)
#   scripts/run_tests.sh golden          # gate de qualidade         (IRIS_EVAL_DB/IRIS_EVAL_QUERIES)
#   scripts/run_tests.sh all             # tudo (só liga portões com dados presentes)
#   scripts/run_tests.sh menu            # escolher interativamente
#
# Caminhos configuráveis por env (com defaults):
#   TEST_DB, IRIS_EVAL_DB, IRIS_EVAL_QUERIES
# Argumentos extras pra pytest passam adiante, ex.: scripts/run_tests.sh standard -x -v

set -uo pipefail
cd "$(dirname "$0")/.."   # raiz do projeto

: "${TEST_DB:=data/meme_compass_full_v1.db}"
: "${IRIS_EVAL_DB:=data/eval/indexes/sample_100.db}"
: "${IRIS_EVAL_QUERIES:=data/eval/golden/golden_30/queries.json}"

PYTEST=(python -m pytest -q -rs)

# Print the top comment block (skip the shebang, stop at the first non-# line).
usage() { awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"; }

run_standard()    { echo "▶ suíte padrão";                 "${PYTEST[@]}" "$@"; }
run_model()       { echo "▶ testes com CLIP (lento/GPU)";  IRIS_RUN_MODEL_TESTS=1 "${PYTEST[@]}" tests/test_ui_interactions.py "$@"; }
run_integration() { echo "▶ dedup pipeline (lento/GPU)";   IRIS_INTEGRATION=1 "${PYTEST[@]}" tests/test_indexer_schema.py "$@"; }

run_db() {
  if [ ! -f "$TEST_DB" ]; then echo "✗ TEST_DB não encontrado: $TEST_DB"; return 1; fi
  echo "▶ API + DB real ($TEST_DB)"
  TEST_DB="$TEST_DB" "${PYTEST[@]}" tests/test_api.py "$@"
}

run_golden() {
  if [ ! -f "$IRIS_EVAL_DB" ]; then
    echo "✗ IRIS_EVAL_DB não encontrado: $IRIS_EVAL_DB"
    echo "  construa com scripts/build_sample_index.py e preencha $IRIS_EVAL_QUERIES"
    return 1
  fi
  echo "▶ golden set ($IRIS_EVAL_DB)"
  IRIS_EVAL_DB="$IRIS_EVAL_DB" IRIS_EVAL_QUERIES="$IRIS_EVAL_QUERIES" \
    "${PYTEST[@]}" tests/test_search_quality.py -k Golden "$@"
}

run_all() {
  echo "▶ TUDO — ligando portões cujos dados existem"
  local env_pairs=(IRIS_RUN_MODEL_TESTS=1 IRIS_INTEGRATION=1)
  if [ -f "$TEST_DB" ]; then env_pairs+=("TEST_DB=$TEST_DB"); else echo "  (pulando DB: $TEST_DB ausente)"; fi
  if [ -f "$IRIS_EVAL_DB" ]; then
    env_pairs+=("IRIS_EVAL_DB=$IRIS_EVAL_DB" "IRIS_EVAL_QUERIES=$IRIS_EVAL_QUERIES")
  else
    echo "  (pulando golden: $IRIS_EVAL_DB ausente)"
  fi
  env "${env_pairs[@]}" "${PYTEST[@]}" "$@"
}

run_menu() {
  echo "Qual conjunto rodar?"
  echo "  1) standard   2) db   3) model   4) integration   5) golden   6) all"
  read -rp "Escolha [1]: " n
  case "${n:-1}" in
    1) run_standard ;; 2) run_db ;; 3) run_model ;;
    4) run_integration ;; 5) run_golden ;; 6) run_all ;;
    *) echo "opção inválida"; return 2 ;;
  esac
}

target="${1:-standard}"; [ $# -gt 0 ] && shift
case "$target" in
  standard)            run_standard "$@" ;;
  db)                  run_db "$@" ;;
  model)               run_model "$@" ;;
  integration|integ)   run_integration "$@" ;;
  golden)              run_golden "$@" ;;
  all)                 run_all "$@" ;;
  menu)                run_menu ;;
  -h|--help|help)      usage ;;
  *) echo "alvo desconhecido: $target"; echo; usage; exit 2 ;;
esac
