"""
Depósitos BP | Tu Renta Fija Real
App web (Streamlit) que muestra, en formato de tarjetas pensado para móvil,
los depósitos a plazo fijo puros y las Letras del Tesoro generados por
buscar_depositos.py, además del informe del "Asesor Financiero".

Requiere: pip install streamlit pandas
Lanzar en local: streamlit run app_web_depositos.py
"""

import html
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
    import streamlit as st
except ImportError as exc:
    print("Faltan dependencias. Instálalas con:\n  pip install streamlit pandas")
    raise SystemExit(1) from exc

CARPETA = Path(__file__).resolve().parent
CSV_PATH = CARPETA / "depositos_activos.csv"
INFORME_PATH = CARPETA / "analisis_estrategico.txt"
SCRIPT_BACKEND = CARPETA / "buscar_depositos.py"

ICONOS_SECCION = {"1": "📈", "2": "🎯", "3": "🪜", "4": "🏆"}

st.set_page_config(
    page_title="Depósitos BP",
    page_icon="🏦",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.3rem;
        padding-bottom: 3rem;
        max-width: 640px;
    }
    .bp-titulo {
        font-size: clamp(1.5rem, 6vw, 2.1rem);
        font-weight: 800;
        margin-bottom: 0.15rem;
        line-height: 1.2;
    }
    .bp-subtitulo {
        font-weight: 400;
        font-size: 0.5em;
        color: #9aa4b2;
        display: block;
        margin-top: 0.15rem;
    }
    div.stButton > button {
        height: 3.3rem;
        font-size: 1.05rem;
        font-weight: 700;
        border-radius: 14px;
    }
    div[data-testid="stMetric"] {
        background: #161b22;
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 0.6rem 0.4rem;
    }
    .bp-card {
        background: linear-gradient(150deg, #161b22 0%, #0d1117 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 1.1rem 1.3rem;
        margin-bottom: 0.9rem;
        box-shadow: 0 4px 18px rgba(0,0,0,0.28);
    }
    .bp-card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 0.4rem;
    }
    .bp-card-banco {
        font-weight: 600;
        font-size: 1rem;
        line-height: 1.3;
    }
    .bp-badge {
        font-size: 0.68rem;
        padding: 0.15rem 0.55rem;
        border-radius: 999px;
        background: rgba(255,255,255,0.08);
        color: #9aa4b2;
        white-space: nowrap;
        flex-shrink: 0;
    }
    .bp-card-tae {
        font-size: clamp(2.1rem, 9vw, 2.6rem);
        font-weight: 800;
        line-height: 1;
        margin: 0.3rem 0 0.75rem 0;
        color: #22c55e;
    }
    .bp-card-tae-simbolo {
        font-size: 0.36em;
        font-weight: 600;
        color: #9aa4b2;
        margin-left: 0.3rem;
    }
    .bp-card-footer {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
    }
    .bp-chip {
        font-size: 0.78rem;
        padding: 0.3rem 0.65rem;
        border-radius: 999px;
        background: rgba(34,197,94,0.15);
        color: #22c55e;
        font-weight: 600;
    }
    .bp-informe-body {
        white-space: pre-wrap;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.85rem;
        line-height: 1.55;
        color: #e6edf3;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _mtime_o_cero(ruta: Path) -> float:
    return ruta.stat().st_mtime if ruta.exists() else 0.0


@st.cache_data(show_spinner=False)
def cargar_datos(_marca_tiempo: float):
    if not CSV_PATH.exists():
        return None
    return pd.read_csv(CSV_PATH, sep=";")


@st.cache_data(show_spinner=False)
def cargar_informe(_marca_tiempo: float):
    if not INFORME_PATH.exists():
        return None
    return INFORME_PATH.read_text(encoding="utf-8")


def parsear_informe(texto: str):
    """
    Separa el informe de texto plano generado por buscar_depositos.py en
    (cabecera, [(titulo_seccion, cuerpo_seccion), ...]) usando las líneas de
    '=' y '-' como delimitadores.
    """
    cabecera_lineas = []
    secciones = []
    titulo_actual = None
    cuerpo_actual = []
    modo = "cabecera"

    for linea in texto.splitlines():
        if re.fullmatch(r"=+", linea.strip()):
            continue
        if re.fullmatch(r"-{5,}", linea.strip()):
            if titulo_actual is not None:
                secciones.append((titulo_actual, "\n".join(cuerpo_actual).strip()))
            titulo_actual = None
            cuerpo_actual = []
            modo = "esperando_titulo"
            continue
        if modo == "esperando_titulo" and linea.strip():
            titulo_actual = linea.strip()
            modo = "cuerpo"
            continue
        if modo == "cabecera":
            cabecera_lineas.append(linea)
        elif modo == "cuerpo":
            cuerpo_actual.append(linea)

    if titulo_actual is not None:
        secciones.append((titulo_actual, "\n".join(cuerpo_actual).strip()))

    return "\n".join(cabecera_lineas).strip(), secciones


# ---------------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------------
st.markdown(
    '<div class="bp-titulo">🏦 Depósitos BP<span class="bp-subtitulo">| Tu Renta Fija Real</span></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Botón de actualización (ejecuta el backend y refresca la web)
# ---------------------------------------------------------------------------
if st.button("🔄 Actualizar Datos del Mercado", use_container_width=True, type="primary"):
    with st.status("Consultando Rankia y Tesoro Público...", expanded=True) as status:
        try:
            resultado = subprocess.run(
                [sys.executable, str(SCRIPT_BACKEND)],
                cwd=str(CARPETA),
                capture_output=True,
                text=True,
                timeout=600,
            )
            if resultado.stdout:
                st.code(resultado.stdout)
            if resultado.returncode != 0:
                status.update(label="Error al actualizar los datos", state="error")
                st.error(resultado.stderr or "El script terminó con un error.")
            else:
                status.update(label="Datos actualizados correctamente", state="complete")
        except Exception as exc:  # noqa: BLE001 - se muestra cualquier fallo al usuario
            status.update(label="Error al actualizar los datos", state="error")
            st.error(str(exc))
    st.cache_data.clear()
    st.rerun()

ultima_actualizacion = (
    datetime.fromtimestamp(CSV_PATH.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    if CSV_PATH.exists()
    else "todavía sin datos"
)
st.caption(f"Última actualización: {ultima_actualizacion}")

df = cargar_datos(_mtime_o_cero(CSV_PATH))
informe_texto = cargar_informe(_mtime_o_cero(INFORME_PATH))

if df is None or df.empty:
    st.warning(
        "Todavía no hay datos guardados. Pulsa **🔄 Actualizar Datos del Mercado** "
        "para generarlos por primera vez."
    )
    st.stop()

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
plazos_validos = df["Plazo (meses)"].dropna()
col1, col2, col3 = st.columns(3)
col1.metric("Mejor TAE", f"{df['TAE (%)'].max():.2f}%")
col2.metric("Ofertas", len(df))
col3.metric(
    "Plazos",
    f"{plazos_validos.min():.0f}-{plazos_validos.max():.0f} m" if not plazos_validos.empty else "—",
)

st.divider()

tab_mercado, tab_asesor = st.tabs(["📊 Mercado", "🧠 Asesor Financiero"])

# ---------------------------------------------------------------------------
# Pestaña "Mercado": tarjetas por depósito / Letra del Tesoro
# ---------------------------------------------------------------------------
with tab_mercado:
    fuentes = ["Todos"] + sorted(df["Fuente"].dropna().unique().tolist())
    filtro_fuente = st.selectbox("Filtrar por fuente", fuentes)
    df_mostrar = df if filtro_fuente == "Todos" else df[df["Fuente"] == filtro_fuente]
    df_mostrar = df_mostrar.sort_values("TAE (%)", ascending=False)

    for _, fila in df_mostrar.iterrows():
        icono = "🏛️" if fila["Fuente"] == "Tesoro Público" else "🏦"
        tae = fila["TAE (%)"]
        plazo = fila["Plazo (meses)"]
        plazo_txt = f"⏳ {plazo:.0f} meses" if pd.notna(plazo) else "⏳ Plazo no especificado"
        garantia = html.escape(str(fila["País del Fondo de Garantía"]))
        banco = html.escape(str(fila["Banco"]))
        fuente = html.escape(str(fila["Fuente"]))

        st.markdown(
            f"""
            <div class="bp-card">
              <div class="bp-card-header">
                <span class="bp-card-banco">{icono} {banco}</span>
                <span class="bp-badge">{fuente}</span>
              </div>
              <div class="bp-card-tae">{tae:.2f}<span class="bp-card-tae-simbolo">% TAE</span></div>
              <div class="bp-card-footer">
                <span class="bp-chip">{plazo_txt}</span>
                <span class="bp-chip">🛡️ {garantia}</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Pestaña "Asesor Financiero": informe de estrategia formateado
# ---------------------------------------------------------------------------
with tab_asesor:
    if not informe_texto:
        st.warning(
            "Todavía no se ha generado el informe. Pulsa "
            "**🔄 Actualizar Datos del Mercado** para crearlo."
        )
    else:
        cabecera, secciones = parsear_informe(informe_texto)

        fecha_match = re.search(r"Generado autom[aá]ticamente el (.+)", cabecera)
        if fecha_match:
            st.caption(f"Informe generado el {fecha_match.group(1).strip()}")

        aviso_match = re.search(r"AVISO:\s*(.+)", cabecera, re.DOTALL)
        if aviso_match:
            st.info(aviso_match.group(1).replace("\n", " ").strip())

        if secciones:
            for titulo, cuerpo in secciones:
                numero = titulo.split(".")[0].strip()
                icono = ICONOS_SECCION.get(numero, "📌")
                titulo_limpio = titulo.split(".", 1)[1].strip() if "." in titulo else titulo
                with st.expander(f"{icono}  {titulo_limpio}", expanded=numero in ("1", "2")):
                    cuerpo_html = html.escape(cuerpo).replace("\n", "<br>")
                    st.markdown(f'<div class="bp-informe-body">{cuerpo_html}</div>', unsafe_allow_html=True)
        else:
            cuerpo_html = html.escape(informe_texto).replace("\n", "<br>")
            st.markdown(f'<div class="bp-informe-body">{cuerpo_html}</div>', unsafe_allow_html=True)
