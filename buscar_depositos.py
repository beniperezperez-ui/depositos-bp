"""
Busca depósitos a plazo fijo PUROS (riesgo 1/6, sin vinculaciones) con TAE > 3%
de bancos que operan en España y están cubiertos por el Fondo de Garantía de
Depósitos español (100.000 € protegidos). Quedan excluidos a propósito los
bancos extranjeros ofrecidos a través de plataformas intermediarias (p. ej.
Raisin), aunque estén cubiertos por el fondo de garantía de su propio país.

Incorpora siempre los tipos vigentes de las Letras del Tesoro español (3, 6,
9 y 12 meses) directamente desde los resultados oficiales del Tesoro Público,
guarda todo en depositos_activos.csv (separado por punto y coma) y genera un
informe de estrategia de ahorro en analisis_estrategico.txt.

Si el scraping en vivo falla o tarda demasiado (típico en producción, por
firewalls o restricciones de red del servidor), se usa como respaldo una
lista de datos reales fijados en septiembre de 2026 (ver DATOS_RESPALDO),
de forma que la app nunca se quede sin datos.

Requiere: pip install requests beautifulsoup4
"""

import csv
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:
    print("Faltan dependencias. Instálalas con:\n  pip install requests beautifulsoup4")
    raise SystemExit(1) from exc

try:
    # tesoro.es usa una cadena de certificados que el bundle de certifi no
    # siempre reconoce en Windows, aunque el almacén de certificados del
    # propio sistema sí la valida. Si está disponible, se usa ese almacén
    # nativo para evitar falsos "SSLCertVerificationError". Se captura
    # cualquier excepción (no solo ImportError): esto es una mejora opcional
    # y nunca debe poder tumbar el script en un servidor donde se comporte
    # de forma distinta (p. ej. un runner Linux de GitHub Actions).
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass

TAE_MINIMA = 3.0
RIESGO_OBJETIVO = "1/6"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 20
PAUSA_ENTRE_PETICIONES = 1.0
TIEMPO_MAXIMO_RANKIA = 60  # segundos; pasado este tiempo se corta la búsqueda en Rankia

RANKIA_COMPARADOR_URL = "https://www.rankia.com/depositos/comparador"
PAISES_FGD_ESPANA = ("españa", "espana")

# Página oficial del Tesoro Público con el resultado de la última subasta de
# Letras a 3, 6, 9 y 12 meses (tabla HTML estática, sin JavaScript).
TESORO_LETRAS_URL = (
    "https://www.tesoro.es/deuda-publica/subastas/resultado-ultimas-subastas/"
    "letras-del-tesoro"
)
PLAZOS_LETRAS = [3, 6, 9, 12]
GARANTIA_LETRAS = "Garantía del Estado Español"

# Señales para descartar depósitos que NO son a plazo fijo puro: productos
# estructurados/combinados por nombre, o cuya TAE anunciada exige vincular
# fondos de inversión, planes de pensiones u otro producto de riesgo.
PALABRAS_EXCLUIDAS_NOMBRE = ("mix", "estructurad", "combinad", "indexad", "referenciad")
PALABRAS_EXCLUIDAS_CONDICIONES = (
    "fondo de inversión",
    "fondos de inversión",
    "patrimonio gestionado",
    "plan de pensiones",
    "planes de pensiones",
    "seguro de vida",
    "suscripción de",
    "vinculac",
)

# Datos reales fijados en septiembre de 2026, usados como respaldo cuando el
# scraping en vivo de Rankia o del Tesoro Público falla o tarda demasiado
# (p. ej. por un firewall en el servidor de producción). Se etiquetan con
# "(respaldo)" en la columna Fuente para distinguirlos de los datos en vivo.
# OJO: son una foto fija de septiembre de 2026; conviene revisarlos de vez en
# cuando para que no queden muy desactualizados.
DATOS_RESPALDO = [
    {
        "Banco": "Letras del Tesoro (España) - 12 meses",
        "Plazo (meses)": 12,
        "TAE (%)": 2.84,
        "País del Fondo de Garantía": GARANTIA_LETRAS,
        "Fuente": "Tesoro Público (respaldo)",
    },
    {
        "Banco": "Letras del Tesoro (España) - 6 meses",
        "Plazo (meses)": 6,
        "TAE (%)": 2.64,
        "País del Fondo de Garantía": "Garantía del Estado Español",
        "Fuente": "Tesoro Público (respaldo)",
    },
    {
        "Banco": "Depósito WiZink 18 meses",
        "Plazo (meses)": 18,
        "TAE (%)": 2.85,
        "País del Fondo de Garantía": "España",
        "Fuente": "Rankia (respaldo)",
    },
    {
        "Banco": "Depósito EBN Banco 24 meses",
        "Plazo (meses)": 24,
        "TAE (%)": 2.70,
        "País del Fondo de Garantía": "España",
        "Fuente": "Rankia (respaldo)",
    },
    {
        "Banco": "Depósito Banca March 12 meses",
        "Plazo (meses)": 12,
        "TAE (%)": 2.50,
        "País del Fondo de Garantía": "España",
        "Fuente": "Rankia (respaldo)",
    },
]


def normaliza_tae(texto):
    """Convierte '3,46%' o '3.46' en 3.46 (float)."""
    texto = texto.replace("%", "").replace(",", ".").strip()
    try:
        return float(texto)
    except ValueError:
        return None


def parse_plazo_meses(texto):
    """Extrae el plazo en meses de un texto tipo 'Depósito a 12 meses'."""
    texto = texto.lower()
    m = re.search(r"(\d+)\s*(?:mes|meses)\b", texto)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(?:año|años)\b", texto)
    if m:
        return int(m.group(1)) * 12
    m = re.search(r"(\d+)\s*(?:d[ií]a|d[ií]as)\b", texto)
    if m:
        return max(1, round(int(m.group(1)) / 30))
    return None


def _extraer_features(card):
    """A partir de una card de Rankia, devuelve un dict {etiqueta: valor}."""
    datos = {}
    bloque = card.select_one(".rnk-ProductComparerCard_Features")
    if not bloque:
        return datos
    for fila in bloque.find_all("div", recursive=False):
        etiqueta_tag = fila.find("b")
        if not etiqueta_tag:
            continue
        etiqueta = etiqueta_tag.get_text(strip=True).rstrip(":")
        etiqueta_tag.extract()
        datos[etiqueta] = fila.get_text(strip=True)
    return datos


def _obtener_detalle_producto_rankia(sesion, url_producto, nombre_producto):
    """
    Visita la ficha del depósito una única vez para:
      (a) comprobar que es un depósito a plazo fijo PURO -no estructurado, no
          combinado y sin exigir vincular fondos de inversión, planes de
          pensiones u otro producto para lograr la TAE anunciada-, y
      (b) leer el nombre del banco emisor.

    Devuelve (es_puro, banco). Si es_puro es False, el candidato se descarta.
    Ante cualquier duda (fallo de red, ficha ilegible) se descarta por
    precaución en vez de incluir un producto sin poder verificarlo.
    """
    nombre_lower = nombre_producto.lower()
    if any(palabra in nombre_lower for palabra in PALABRAS_EXCLUIDAS_NOMBRE):
        return False, None

    try:
        resp = sesion.get(url_producto, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return False, None

    soup = BeautifulSoup(resp.text, "html.parser")

    # La propia fila "TAE (%)" de la ficha indica si el tipo anunciado exige
    # cumplir condiciones (p. ej. "4,00% cumpliendo condiciones").
    for subtitulo in soup.select(".rnk-ProductInfoBlock_NewSubtitle"):
        if "tae" in subtitulo.get_text(strip=True).lower():
            contenido = subtitulo.find_next_sibling("p", class_="rnk-ProductInfoBlock_Content")
            if contenido and "condici" in contenido.get_text(strip=True).lower():
                return False, None
            break

    # El bloque de "Información adicional" no debe exigir vinculaciones.
    texto_adicional = " ".join(
        bloque.get_text(" ", strip=True).lower()
        for bloque in soup.select(".rnk-ProductInfoBlock-full-width")
    )
    if any(palabra in texto_adicional for palabra in PALABRAS_EXCLUIDAS_CONDICIONES):
        return False, None

    subtitulo_banco = soup.select_one("h2.rnk-Hero_Subtitle")
    banco = subtitulo_banco.get_text(strip=True) if subtitulo_banco else None
    return True, banco


def obtener_depositos_rankia(sesion):
    """
    Recorre el comparador público de depósitos de Rankia y se queda con las
    ofertas de riesgo 1/6, TAE superior al 3%, garantizadas por el Fondo de
    Garantía de Depósitos ESPAÑOL (se descartan bancos extranjeros, aunque
    Rankia los liste) y que sean depósitos a plazo fijo puros: se excluyen
    los estructurados, combinados o que exijan vincular fondos de inversión,
    seguros o planes de pensiones para lograr la TAE anunciada.

    El robots.txt de rankia.com excluye explícitamente el parámetro de query
    'orden=' (Disallow: /*orden=), así que a propósito NO se usa para pedir
    la lista pre-ordenada por TAE: se recorren todas las páginas del
    comparador en su orden por defecto y se filtra el resultado igualmente.
    """
    print("Consultando Rankia...")
    resultados = []

    try:
        primera = sesion.get(RANKIA_COMPARADOR_URL, params={"page": 1}, timeout=TIMEOUT)
        primera.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Aviso: no se pudo consultar Rankia ({exc}). Se omite esta fuente.")
        return resultados

    soup = BeautifulSoup(primera.text, "html.parser")
    contenedor = soup.select_one(".rnk-ProductComparer")
    if not contenedor:
        print("  Aviso: no se pudo leer el comparador de Rankia. Se omite esta fuente.")
        return resultados

    total = int(contenedor.get("data-total", 0) or 0)
    por_pagina = int(contenedor.get("data-count", 12) or 12)
    total_paginas = max(1, math.ceil(total / por_pagina)) if total else 1
    print(
        f"  {total} depósitos en {total_paginas} páginas; filtrando riesgo "
        f"{RIESGO_OBJETIVO}, garantía España y TAE > {TAE_MINIMA}%..."
    )

    inicio = time.monotonic()
    candidatos = []
    for pagina in range(1, total_paginas + 1):
        if time.monotonic() - inicio > TIEMPO_MAXIMO_RANKIA:
            print(
                f"  Aviso: se ha superado el tiempo máximo de búsqueda "
                f"({TIEMPO_MAXIMO_RANKIA}s) en la página {pagina}; se corta aquí."
            )
            break
        if pagina == 1:
            html = primera.text
        else:
            time.sleep(PAUSA_ENTRE_PETICIONES)
            try:
                resp = sesion.get(
                    RANKIA_COMPARADOR_URL, params={"page": pagina}, timeout=TIMEOUT
                )
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException as exc:
                print(f"  Aviso: fallo en la página {pagina} de Rankia ({exc}), se omite.")
                continue

        pagina_soup = BeautifulSoup(html, "html.parser")
        for card in pagina_soup.select(".rnk-ProductComparerCard"):
            riesgo_tag = card.select_one(".rnk-ProductComparerCard_RiskIndex")
            riesgo = riesgo_tag.get_text(strip=True) if riesgo_tag else None
            if riesgo != RIESGO_OBJETIVO:
                continue

            datos = _extraer_features(card)
            tae = normaliza_tae(datos.get("TAE", ""))
            pais = datos.get("Fondo de garantía")
            if tae is None or tae <= TAE_MINIMA or not pais:
                continue
            if pais.strip().lower() not in PAISES_FGD_ESPANA:
                # Solo bancos cubiertos por el Fondo de Garantía de Depósitos
                # español: se descartan bancos extranjeros aunque coticen en
                # el comparador de Rankia.
                continue

            enlace = card.select_one("h2.rnk-ProductComparerCard_Name a")
            if not enlace or not enlace.get("href"):
                continue

            nombre_producto = enlace.get_text(strip=True)
            candidatos.append(
                {
                    "url": urljoin(RANKIA_COMPARADOR_URL, enlace["href"]),
                    "nombre_producto": nombre_producto,
                    "plazo_meses": parse_plazo_meses(nombre_producto),
                    "tae": tae,
                    "pais": pais.strip(),
                }
            )

    excluidos_vinculados = 0
    for candidato in candidatos:
        if time.monotonic() - inicio > TIEMPO_MAXIMO_RANKIA:
            print(
                f"  Aviso: se ha superado el tiempo máximo de búsqueda "
                f"({TIEMPO_MAXIMO_RANKIA}s); se deja de verificar fichas restantes."
            )
            break
        time.sleep(PAUSA_ENTRE_PETICIONES)
        es_puro, banco = _obtener_detalle_producto_rankia(
            sesion, candidato["url"], candidato["nombre_producto"]
        )
        if not es_puro:
            excluidos_vinculados += 1
            continue

        plazo_meses = candidato["plazo_meses"]
        if plazo_meses is None and banco:
            plazo_meses = parse_plazo_meses(banco)
        if not banco:
            banco = candidato["nombre_producto"]

        resultados.append(
            {
                "Banco": banco.strip(),
                "Plazo (meses)": plazo_meses,
                "TAE (%)": round(candidato["tae"], 2),
                "País del Fondo de Garantía": candidato["pais"],
                "Fuente": "Rankia",
            }
        )

    if excluidos_vinculados:
        print(
            f"  {excluidos_vinculados} ofertas descartadas por ser estructuradas, "
            "combinadas o exigir vinculaciones (fondos, planes de pensiones, etc.)."
        )
    print(f"  {len(resultados)} depósitos de Rankia cumplen los criterios.")
    return resultados


def _fila_tabla_tesoro(soup, prefijo_id):
    """Devuelve los 4 valores (3/6/9/12 meses) de una fila de la tabla de subastas."""
    cabecera = soup.find("th", id=lambda x: x and x.startswith(prefijo_id))
    if not cabecera:
        return None
    fila = cabecera.find_parent("tr")
    if not fila:
        return None
    return [celda.get_text(strip=True) for celda in fila.find_all("td")]


def obtener_letras_tesoro(sesion):
    """
    Descarga de tesoro.es el resultado de la última subasta de Letras del
    Tesoro a 3, 6, 9 y 12 meses. Se incluyen SIEMPRE en el resultado final,
    superen o no el umbral de TAE_MINIMA usado para depósitos bancarios,
    porque están garantizadas por el Estado y sirven de referencia obligada.

    Además devuelve, por plazo, el tipo marginal de esta subasta frente al de
    la anterior: esa comparación es la que usa el informe de estrategia para
    determinar si los tipos de interés están subiendo o bajando.
    """
    print("Consultando Tesoro Público (Letras del Tesoro)...")
    resultados = []
    tendencia = {}

    try:
        resp = sesion.get(TESORO_LETRAS_URL, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  Aviso: no se pudo consultar el Tesoro Público ({exc}).")
        return resultados, tendencia

    soup = BeautifulSoup(resp.text, "html.parser")
    fechas = _fila_tabla_tesoro(soup, "view-field-fecha-subasta")
    marginales = _fila_tabla_tesoro(soup, "view-field-tipo-de-inter-s-marginal")
    medios = _fila_tabla_tesoro(soup, "view-field-tipo-de-inter-s-medio")
    anteriores = _fila_tabla_tesoro(soup, "view-field-anterior-tipo-marginal")

    if not medios or len(medios) < len(PLAZOS_LETRAS):
        print("  Aviso: no se pudo leer la tabla de subastas de Letras del Tesoro.")
        return resultados, tendencia

    for i, plazo in enumerate(PLAZOS_LETRAS):
        tae_medio = normaliza_tae(medios[i])
        if tae_medio is None:
            continue

        resultados.append(
            {
                "Banco": f"Letras del Tesoro (España) - {plazo} meses",
                "Plazo (meses)": plazo,
                "TAE (%)": round(tae_medio, 2),
                "País del Fondo de Garantía": GARANTIA_LETRAS,
                "Fuente": "Tesoro Público",
            }
        )

        tendencia[plazo] = {
            "tae_medio": tae_medio,
            "marginal_actual": normaliza_tae(marginales[i]) if marginales else None,
            "marginal_anterior": normaliza_tae(anteriores[i]) if anteriores else None,
            "fecha_subasta": fechas[i] if fechas else None,
        }

    print(f"  {len(resultados)} plazos de Letras del Tesoro incorporados (obligatorio).")
    return resultados, tendencia


def analizar_tendencia(tendencia_letras):
    """
    Compara, plazo a plazo, el tipo marginal de la última subasta de Letras
    con el de la subasta anterior. Con esa variación media clasifica la
    tendencia de tipos como 'subiendo', 'bajando' o 'estable'.
    """
    UMBRAL_PP = 0.02  # puntos porcentuales de margen antes de considerar "estable"
    detalle = []
    for plazo, info in sorted(tendencia_letras.items()):
        actual = info.get("marginal_actual")
        anterior = info.get("marginal_anterior")
        if actual is None or anterior is None:
            continue
        detalle.append((plazo, anterior, actual, round(actual - anterior, 3)))

    if not detalle:
        return "indeterminada", 0.0, detalle

    delta_medio = round(sum(d[3] for d in detalle) / len(detalle), 3)
    if delta_medio > UMBRAL_PP:
        return "subiendo", delta_medio, detalle
    if delta_medio < -UMBRAL_PP:
        return "bajando", delta_medio, detalle
    return "estable", delta_medio, detalle


def _mejor_oferta_por_plazo(registros, plazo_objetivo, tolerancia):
    candidatas = [
        r
        for r in registros
        if r["Plazo (meses)"] is not None
        and abs(r["Plazo (meses)"] - plazo_objetivo) <= tolerancia
    ]
    if not candidatas:
        return None
    return max(candidatas, key=lambda r: r["TAE (%)"])


def _euros(cantidad):
    return f"{cantidad:,.0f} €".replace(",", ".")


def generar_informe_estrategico(registros, tendencia_letras, ruta_salida):
    """Genera un informe de texto con lectura de tendencia, plazo recomendado,
    reparto en escalera y las mejores ofertas disponibles."""
    tendencia, delta_medio, detalle = analizar_tendencia(tendencia_letras)

    lineas = []
    lineas.append("=" * 72)
    lineas.append("INFORME DE ESTRATEGIA DE AHORRO - DEPÓSITOS Y LETRAS DEL TESORO")
    lineas.append(f"Generado automáticamente el {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    lineas.append("=" * 72)
    lineas.append("")
    lineas.append(
        "AVISO: informe automático orientativo generado a partir de datos públicos\n"
        "de mercado (Rankia y Tesoro Público), limitado a bancos que operan en\n"
        "España y depósitos a plazo fijo puros. No es asesoramiento financiero\n"
        "profesional ni una recomendación de inversión personalizada; valora tu\n"
        "propia situación fiscal y de liquidez antes de decidir."
    )
    lineas.append("")

    fuentes_respaldo = sorted({r["Fuente"] for r in registros if "(respaldo)" in r["Fuente"]})
    if fuentes_respaldo:
        lineas.append(
            "⚠️ Aviso: no se pudo conectar en directo con: " + ", ".join(fuentes_respaldo) + ".\n"
            "Se están mostrando datos de referencia fijados en septiembre de 2026,\n"
            "que pueden no reflejar el mercado en este momento. Pulsa de nuevo\n"
            "'Actualizar Datos del Mercado' más tarde para reintentar la conexión en vivo."
        )
        lineas.append("")

    lineas.append("-" * 72)
    lineas.append("1. TENDENCIA ACTUAL DE LOS TIPOS DE INTERÉS")
    lineas.append("-" * 72)
    lineas.append(
        "Base: tipo marginal de la última subasta de Letras del Tesoro frente al\n"
        "de la subasta inmediatamente anterior, para cada plazo."
    )
    lineas.append("")
    if detalle:
        for plazo, anterior, actual, delta in detalle:
            signo = "+" if delta >= 0 else ""
            lineas.append(
                f"  Letras a {plazo:>2} meses: {anterior:.3f}% -> {actual:.3f}%  ({signo}{delta:.3f} p.p.)"
            )
    else:
        lineas.append("  No se pudo obtener la comparación con la subasta anterior.")
    lineas.append("")

    if tendencia == "subiendo":
        lineas.append(
            f"Diagnóstico: los tipos de interés están SUBIENDO (variación media "
            f"{delta_medio:+.3f} p.p. entre subastas consecutivas), un patrón\n"
            "propio de un contexto de inflación persistente o de tipos oficiales\n"
            "todavía al alza."
        )
    elif tendencia == "bajando":
        lineas.append(
            f"Diagnóstico: los tipos de interés están BAJANDO (variación media "
            f"{delta_medio:+.3f} p.p. entre subastas consecutivas), un patrón\n"
            "propio de una fase de relajación monetaria (bajadas de tipos oficiales)."
        )
    else:
        lineas.append(
            f"Diagnóstico: los tipos de interés están ESTABLES (variación media "
            f"{delta_medio:+.3f} p.p., dentro del margen de ruido)."
        )
    lineas.append("")

    lineas.append("-" * 72)
    lineas.append("2. RECOMENDACIÓN DE PLAZO")
    lineas.append("-" * 72)
    if tendencia == "subiendo":
        lineas.append(
            "Con tipos al alza conviene priorizar PLAZOS CORTOS (3-6 meses): se\n"
            "recupera el capital pronto y se puede reinvertir cuando el mercado\n"
            "ofrezca una TAE todavía mayor, en vez de quedar atado a un tipo que\n"
            "en pocos meses quedará por debajo del de mercado."
        )
    elif tendencia == "bajando":
        lineas.append(
            "Con tipos a la baja conviene priorizar PLAZOS LARGOS (12-24 meses):\n"
            "así se bloquea ('lock-in') el tipo actual, todavía alto, antes de que\n"
            "las próximas renovaciones u ofertas nuevas vengan con una TAE menor."
        )
    else:
        lineas.append(
            "Con tipos estables no hay una ventaja clara de plazo por tendencia:\n"
            "mantener una escalera equilibrada entre plazos cortos y largos es la\n"
            "opción más prudente."
        )
    lineas.append("")

    lineas.append("-" * 72)
    lineas.append("3. ESTRATEGIA DE LA ESCALERA (LADDERING) - SEPTIEMBRE 2026")
    lineas.append("-" * 72)
    lineas.append(
        "La escalera reparte el capital entre varios plazos en vez de ponerlo\n"
        "todo a un único vencimiento: siempre queda una parte liberándose pronto\n"
        "(liquidez y capacidad de reinversión) y otra parte aprovechando el tipo\n"
        "más alto de los plazos largos, sin depender de acertar el momento exacto\n"
        "del mercado."
    )
    lineas.append("")

    if tendencia == "subiendo":
        reparto = [(3, 40), (6, 30), (9, 20), (12, 10)]
        nota = "Escalera desplazada hacia el CORTO plazo, por la tendencia alcista de tipos."
    elif tendencia == "bajando":
        reparto = [(3, 15), (6, 20), (12, 30), (24, 35)]
        nota = "Escalera desplazada hacia el LARGO plazo, por la tendencia bajista de tipos."
    else:
        reparto = [(3, 25), (6, 25), (12, 25), (24, 25)]
        nota = "Escalera equilibrada a partes iguales, por tendencia estable/indeterminada."

    lineas.append(nota)
    lineas.append("")
    capital_ejemplo = 10000
    lineas.append(f"Ejemplo de reparto sobre un capital ilustrativo de {_euros(capital_ejemplo)}")
    lineas.append("(ajusta las cantidades de forma proporcional a tu capital real):")
    lineas.append("")
    for plazo, porcentaje in reparto:
        importe = capital_ejemplo * porcentaje / 100
        tolerancia = 3 if plazo >= 24 else 1
        mejor = _mejor_oferta_por_plazo(registros, plazo, tolerancia)
        lineas.append(f"  - {porcentaje}% ({_euros(importe)}) a {plazo} meses")
        if mejor:
            lineas.append(
                f"      Mejor opción actual: {mejor['Banco']} - {mejor['TAE (%)']}% TAE "
                f"({mejor['Fuente']}, garantía: {mejor['País del Fondo de Garantía']})"
            )
        else:
            lineas.append("      No se ha encontrado ninguna oferta activa para este plazo exacto.")
    lineas.append("")

    lineas.append("-" * 72)
    lineas.append("4. RESUMEN DE LAS 10 MEJORES OFERTAS DISPONIBLES (todos los plazos)")
    lineas.append("-" * 72)
    if not any(r["Fuente"].startswith("Rankia") and r["TAE (%)"] > TAE_MINIMA for r in registros):
        lineas.append(
            "Nota: en la fecha de este informe ningún depósito bancario español\n"
            "puro (sin vinculaciones a fondos, seguros o planes de pensiones) supera\n"
            "el 3% TAE con el Fondo de Garantía de Depósitos español. Por eso todas\n"
            "las 'mejores opciones' de este informe son Letras del Tesoro, que hoy\n"
            "ofrecen más rentabilidad que los depósitos puros disponibles."
        )
        lineas.append("")
    for r in sorted(registros, key=lambda x: x["TAE (%)"], reverse=True)[:10]:
        plazo_txt = f"{r['Plazo (meses)']:>3} meses" if r["Plazo (meses)"] is not None else "  ?  "
        lineas.append(
            f"  {r['TAE (%)']:>5.2f}% TAE | {plazo_txt} | {r['Banco']} "
            f"({r['Fuente']}, {r['País del Fondo de Garantía']})"
        )
    lineas.append("")
    lineas.append("=" * 72)

    with open(ruta_salida, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))


def guardar_csv(depositos, ruta_salida):
    depositos_ordenados = sorted(depositos, key=lambda d: d["TAE (%)"], reverse=True)

    vistos = set()
    unicos = []
    for d in depositos_ordenados:
        clave = (d["Banco"].lower(), d["Plazo (meses)"], d["TAE (%)"])
        if clave in vistos:
            continue
        vistos.add(clave)
        unicos.append(d)

    columnas = ["Banco", "Plazo (meses)", "TAE (%)", "País del Fondo de Garantía", "Fuente"]
    with open(ruta_salida, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, delimiter=";")
        writer.writeheader()
        writer.writerows(unicos)

    return unicos


def main():
    carpeta = Path(__file__).resolve().parent
    salida_csv = carpeta / "depositos_activos.csv"
    salida_informe = carpeta / "analisis_estrategico.txt"

    sesion = requests.Session()
    sesion.headers.update(HEADERS)

    # Cada fuente va envuelta en su propio try/except: un fallo inesperado en
    # una (p. ej. un cambio en el HTML del sitio, o un bloqueo de red distinto
    # a los ya controlados con requests.RequestException) nunca debe tumbar
    # todo el script, sino recurrir al respaldo de esa fuente y seguir.
    try:
        depositos_rankia = obtener_depositos_rankia(sesion)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo cae al respaldo
        print(f"  Aviso: fallo inesperado consultando Rankia ({type(exc).__name__}: {exc}).")
        depositos_rankia = []
    if not depositos_rankia:
        respaldo_rankia = [d for d in DATOS_RESPALDO if d["Fuente"].startswith("Rankia")]
        print(
            f"  Aviso: no se obtuvo ningún depósito en vivo de Rankia; se usan "
            f"{len(respaldo_rankia)} datos de respaldo (septiembre 2026)."
        )
        depositos_rankia = respaldo_rankia

    try:
        letras, tendencia_letras = obtener_letras_tesoro(sesion)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo cae al respaldo
        print(f"  Aviso: fallo inesperado consultando el Tesoro Público ({type(exc).__name__}: {exc}).")
        letras, tendencia_letras = [], {}
    if not letras:
        letras = [d for d in DATOS_RESPALDO if d["Fuente"].startswith("Tesoro")]
        tendencia_letras = {}
        print(
            f"  Aviso: no se obtuvieron Letras del Tesoro en vivo; se usan "
            f"{len(letras)} datos de respaldo (septiembre 2026)."
        )

    depositos = depositos_rankia + letras

    if not depositos:
        # Red de seguridad final: en la práctica nunca debería llegar aquí,
        # porque DATOS_RESPALDO siempre aporta registros para ambas fuentes.
        print("  Aviso: no había ningún dato disponible; se usa el respaldo completo.")
        depositos = list(DATOS_RESPALDO)

    try:
        finales = guardar_csv(depositos, salida_csv)
        print(f"\nGuardados {len(finales)} registros en: {salida_csv}")
    except OSError as exc:
        print(f"Error: no se pudo escribir {salida_csv} ({exc}).")
        sys.exit(1)

    try:
        generar_informe_estrategico(finales, tendencia_letras, salida_informe)
        print(f"Informe de estrategia generado en: {salida_informe}")
    except Exception as exc:  # noqa: BLE001 - el CSV ya se guardó; no se debe fallar por el informe
        print(f"  Aviso: no se pudo generar el informe de estrategia ({type(exc).__name__}: {exc}).")


if __name__ == "__main__":
    main()
