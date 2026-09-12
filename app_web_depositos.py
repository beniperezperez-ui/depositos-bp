"""
Depósitos BP | Tu Renta Fija Real
App web (Streamlit) que muestra, en formato de tarjetas pensado para móvil,
los depósitos a plazo fijo puros y las Letras del Tesoro generados por
buscar_depositos.py, además del informe del "Asesor Financiero".

Los datos se actualizan solos: el flujo de GitHub Actions en
.github/workflows/actualizar_mercado.yml ejecuta buscar_depositos.py todas
las mañanas y sube los ficheros nuevos al repositorio, así que esta app es
un simple visor de solo lectura (sin botones) que siempre lee la última
versión de depositos_activos.csv y analisis_estrategico.txt.

Requiere: pip install streamlit pandas
Lanzar en local: streamlit run app_web_depositos.py
"""

import html
import re
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

ICONOS_SECCION = {"1": "📈", "2": "🎯", "3": "🪜", "4": "🏆"}

st.set_page_config(
    page_title="Depósitos BP",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root {
        --bp-verde: #22c55e;
        --bp-verde-oscuro: #15803d;
        --bp-azul: #3b82f6;
        --bp-azul-oscuro: #1d4ed8;
        --bp-fondo-tarjeta: linear-gradient(160deg, #1a2029 0%, #10141b 100%);
        --bp-borde: rgba(255,255,255,0.09);
        --bp-texto: #f3f5f7;
        --bp-texto-tenue: #9aa4b2;
    }

    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 3rem;
        padding-left: 1.25rem;
        padding-right: 1.25rem;
        max-width: 920px;
        margin-left: auto;
        margin-right: auto;
    }

    /* ---------- Cabecera corporativa (a prueba de recortes en móvil) ---------- */
    .bp-header {
        display: flex;
        align-items: center;
        gap: 1rem;
        flex-wrap: wrap;
        margin-bottom: 0.4rem;
    }
    .bp-header-badge {
        flex: 0 0 auto;
        width: 3.4rem;
        height: 3.4rem;
        border-radius: 18px;
        background: linear-gradient(135deg, var(--bp-verde), var(--bp-verde-oscuro));
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.8rem;
        box-shadow: 0 8px 18px rgba(34,197,94,0.35);
    }
    .bp-header-text {
        min-width: 0;
        flex: 1 1 240px;
    }
    .bp-header-title {
        font-size: clamp(1.35rem, 5vw, 2rem);
        font-weight: 800;
        line-height: 1.15;
        letter-spacing: -0.01em;
        color: var(--bp-texto);
        margin: 0;
        overflow-wrap: break-word;
    }
    .bp-header-subtitle {
        font-size: clamp(0.72rem, 2.6vw, 0.9rem);
        font-weight: 700;
        color: var(--bp-verde);
        letter-spacing: 0.06em;
        text-transform: uppercase;
        margin-top: 0.15rem;
    }
    .bp-auto-badge {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        font-size: 0.75rem;
        font-weight: 600;
        color: var(--bp-texto-tenue);
        background: rgba(255,255,255,0.05);
        border: 1px solid var(--bp-borde);
        border-radius: 999px;
        padding: 0.35rem 0.8rem;
        margin: 1rem 0 0.6rem 0;
    }

    /* ---------- KPIs ---------- */
    div[data-testid="stMetric"] {
        background: var(--bp-fondo-tarjeta);
        border: 1px solid var(--bp-borde);
        border-radius: 16px;
        padding: 0.7rem 0.5rem;
        box-shadow: 0 6px 16px rgba(0,0,0,0.25);
    }

    /* ---------- Tarjetas de depósito ---------- */
    .bp-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        background: var(--bp-fondo-tarjeta);
        border: 1px solid var(--bp-borde);
        border-radius: 22px;
        padding: 1.2rem 1.3rem;
        margin-bottom: 1rem;
        box-shadow: 0 12px 26px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04);
    }
    .bp-card-info {
        min-width: 0;
        flex: 1 1 auto;
    }
    .bp-card-banco {
        font-weight: 700;
        font-size: 1.02rem;
        color: var(--bp-texto);
        margin: 0 0 0.55rem 0;
        overflow-wrap: break-word;
    }
    .bp-card-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-bottom: 0.5rem;
    }
    .bp-chip {
        font-size: 0.72rem;
        padding: 0.28rem 0.65rem;
        border-radius: 999px;
        background: rgba(34,197,94,0.14);
        color: #4ade80;
        font-weight: 600;
        white-space: nowrap;
    }
    .bp-chip-outline {
        background: transparent;
        border: 1px solid rgba(255,255,255,0.16);
        color: var(--bp-texto-tenue);
    }
    .bp-card-fuente {
        font-size: 0.65rem;
        color: #6b7280;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    /* Círculo de TAE llamativo, a la derecha de la tarjeta */
    .bp-circulo {
        flex: 0 0 auto;
        width: 5.6rem;
        height: 5.6rem;
        border-radius: 50%;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
        line-height: 1;
    }
    .bp-circulo-alta {
        background: radial-gradient(circle at 30% 28%, #86efac, #15803d 75%);
        color: #052e16;
        box-shadow: 0 8px 20px rgba(21,128,61,0.5), inset 0 2px 6px rgba(255,255,255,0.3);
    }
    .bp-circulo-normal {
        background: radial-gradient(circle at 30% 28%, #93c5fd, #1d4ed8 75%);
        color: #0b1220;
        box-shadow: 0 8px 20px rgba(29,78,216,0.45), inset 0 2px 6px rgba(255,255,255,0.25);
    }
    .bp-circulo-valor {
        font-size: 1.3rem;
        font-weight: 800;
    }
    .bp-circulo-simbolo {
        font-size: 0.56rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        margin-top: 0.2rem;
    }

    /* ---------- Informe del Asesor Financiero ---------- */
    .bp-informe-body {
        white-space: pre-wrap;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        font-size: 0.85rem;
        line-height: 1.55;
        color: var(--bp-texto);
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
# Cabecera corporativa
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="bp-header">
      <div class="bp-header-badge">🏦</div>
      <div class="bp-header-text">
        <p class="bp-header-title">Depósitos BP</p>
        <p class="bp-header-subtitle">Tu Renta Fija Real</p>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

ultima_actualizacion = (
    datetime.fromtimestamp(CSV_PATH.stat().st_mtime).strftime("%d/%m/%Y a las %H:%M")
    if CSV_PATH.exists()
    else None
)
if ultima_actualizacion:
    st.markdown(
        f'<div class="bp-auto-badge">🕘 Actualización automática diaria a las 09:00 '
        f'(España) &middot; última: {ultima_actualizacion}</div>',
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="bp-auto-badge">🕘 Actualización automática diaria a las 09:00 (España)</div>',
        unsafe_allow_html=True,
    )

df = cargar_datos(_mtime_o_cero(CSV_PATH))
informe_texto = cargar_informe(_mtime_o_cero(INFORME_PATH))

if df is None or df.empty:
    st.warning(
        "Todavía no hay datos guardados. Se generarán automáticamente en la "
        "próxima ejecución programada (09:00, hora de España)."
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
        icono = "🏛️" if str(fila["Fuente"]).startswith("Tesoro") else "🏦"
        tae = fila["TAE (%)"]
        plazo = fila["Plazo (meses)"]
        plazo_txt = f"⏳ {plazo:.0f} meses" if pd.notna(plazo) else "⏳ Plazo no especificado"
        garantia = html.escape(str(fila["País del Fondo de Garantía"]))
        banco = html.escape(str(fila["Banco"]))
        fuente = html.escape(str(fila["Fuente"]))
        clase_circulo = "bp-circulo-alta" if tae >= 3 else "bp-circulo-normal"

        st.markdown(
            f"""
            <div class="bp-card">
              <div class="bp-card-info">
                <p class="bp-card-banco">{icono} {banco}</p>
                <div class="bp-card-tags">
                  <span class="bp-chip">{plazo_txt}</span>
                  <span class="bp-chip bp-chip-outline">🛡️ {garantia}</span>
                </div>
                <span class="bp-card-fuente">{fuente}</span>
              </div>
              <div class="bp-circulo {clase_circulo}">
                <span class="bp-circulo-valor">{tae:.2f}%</span>
                <span class="bp-circulo-simbolo">TAE</span>
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
        st.warning("Todavía no se ha generado el informe de estrategia.")
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
