"""Shared Streamlit UI components for the Iris media search app.

All rendering primitives live here so tab modules stay focused on their domain.
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

import cv2
import numpy as np
import streamlit as st
from PIL import Image

from core.file_ops import to_file_uri
from core.search_engine import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


# ── Session state helpers ────────────────────────────────────────────────────


def selection_key(index: int) -> str:
    return f"select_{index}"


def clear_selection_state() -> None:
    for key in list(st.session_state):
        if key.startswith("select_"):
            st.session_state[key] = False


def clear_video_state() -> None:
    for key in list(st.session_state):
        if key.startswith("vid_loaded_"):
            del st.session_state[key]


# ── Media rendering ──────────────────────────────────────────────────────────


def is_blank_frame(frame_bgr: np.ndarray) -> bool:
    """True if the frame is all-black, all-white, or solid colour (no visual content)."""
    try:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        mean = float(gray.mean())
        std = float(gray.std())
        return std < 12.0 or mean < 8.0 or mean > 247.0
    except Exception:
        return True


@st.cache_data(max_entries=1000, ttl=3600)
def video_thumbnail(file_path: str) -> bytes | None:
    """Extract a video thumbnail: try frame 0 first, fall back to 1/4 position."""
    try:
        cap = cv2.VideoCapture(file_path)
        total = max(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), 1)

        candidates = [0, total // 4, total // 2, total // 8]
        chosen_frame = None
        for pos in candidates:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(pos, 0))
            ok, frame = cap.read()
            if ok and not is_blank_frame(frame):
                chosen_frame = frame
                break

        cap.release()
        if chosen_frame is None:
            return None

        frame_rgb = cv2.cvtColor(chosen_frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        pil_img.thumbnail((480, 480))
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:
        return None


@st.cache_data(max_entries=500)
def thumb_b64(file_path: str, is_video: bool) -> str:
    """80×80 JPEG thumbnail as base64 string. Cached by path."""
    try:
        if is_video:
            data = video_thumbnail(file_path)
            return base64.b64encode(data).decode() if data else ""
        img = Image.open(file_path).convert("RGB")
        img.thumbnail((80, 80))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def render_media(
    file_path: str | None,
    exists: bool,
    ext: str,
    item_key: str,
) -> None:
    """Render an image or video with lazy loading and error handling."""
    if not exists or not file_path:
        st.warning("Arquivo nao encontrado.")
        return
    if ext in VIDEO_EXTENSIONS:
        vid_key = f"vid_loaded_{item_key}"
        if not st.session_state.get(vid_key):
            thumb = video_thumbnail(file_path)
            if thumb:
                st.image(thumb, use_container_width=True)
            else:
                st.caption("🎬")
            if st.button("▶ Reproduzir", key=f"play_{item_key}"):
                st.session_state[vid_key] = True
                st.rerun()
        else:
            try:
                st.video(file_path)
            except Exception:
                st.warning("Nao foi possivel reproduzir.")
                try:
                    st.link_button("Abrir no player", to_file_uri(file_path))
                except Exception:
                    pass
    else:
        try:
            st.image(file_path, use_container_width=True)
        except Exception:
            st.warning("Nao foi possivel exibir a imagem.")
            try:
                st.link_button("Abrir arquivo", to_file_uri(file_path))
            except Exception:
                pass


def render_floating_selection_panel(n_selected: int, thumbs_html: str) -> None:
    """Fixed floating panel in bottom-right corner showing selection count and thumbnails."""
    uid = f"mcf{n_selected}"
    st.markdown(
        f"""
        <style>
        #{uid}-chk {{ display:none; }}
        #{uid}-panel {{ display:none; }}
        #{uid}-chk:checked ~ #{uid}-panel {{ display:block; }}
        #{uid}-badge {{ cursor:pointer; user-select:none; }}
        </style>
        <div style="position:fixed;bottom:22px;right:22px;z-index:99999;
                    display:flex;flex-direction:column-reverse;align-items:flex-end;gap:8px;">
          <input type="checkbox" id="{uid}-chk">
          <label for="{uid}-chk" id="{uid}-badge" style="
              background:#e63946;color:#fff;border-radius:50px;
              padding:10px 20px;font-size:14px;font-weight:700;
              box-shadow:0 4px 16px rgba(0,0,0,.4);white-space:nowrap;
              font-family:sans-serif;display:inline-block;">
            🗂 {n_selected} selecionado(s)
          </label>
          <div id="{uid}-panel" style="
              background:#16213e;border:1px solid #3a3a5c;border-radius:14px;
              padding:14px;max-width:340px;
              box-shadow:0 6px 24px rgba(0,0,0,.5);font-family:sans-serif;">
            <div style="color:#fff;font-weight:700;font-size:13px;margin-bottom:8px;">
              Itens selecionados
            </div>
            <div style="display:flex;flex-wrap:wrap;">{thumbs_html}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── Feedback rendering ───────────────────────────────────────────────────────


def render_trash_feedback() -> None:
    feedback = st.session_state.pop("trash_feedback", None)
    if not feedback:
        return
    moved = feedback.get("moved", [])
    failed = feedback.get("failed", [])
    if moved:
        st.success(f"{len(moved)} arquivo(s) enviado(s) para a lixeira.")
    if failed:
        st.error(f"{len(failed)} arquivo(s) nao puderam ser movidos.")
        with st.expander("Falhas na lixeira"):
            st.json([{"arquivo": path, "erro": error} for path, error in failed])


def render_import_feedback() -> None:
    feedback = st.session_state.pop("import_feedback", None)
    if not feedback:
        return
    if feedback.get("ok"):
        st.success(str(feedback.get("message", "Importacao concluida.")))
    else:
        st.error(str(feedback.get("message", "Importacao falhou.")))
