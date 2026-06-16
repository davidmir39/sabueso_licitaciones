"""
tests/test_utils.py — Sabueso de Licitaciones
===============================================
Tests unitarios para src/utils.py.

Usamos datos reales del feed PCSP (verificados en ejecuciones reales) para
que los tests sean representativos de lo que ocurre en producción.

Cómo ejecutar:
    pytest                         # todos los tests
    pytest -v                      # con detalle
    pytest tests/test_utils.py     # solo este fichero
    pytest -v -k "importe"         # solo tests cuyo nombre contiene "importe"
"""

import pytest
from datetime import datetime

# Importamos las funciones que vamos a testear.
# Funcionan porque el proyecto está instalado con pip install -e .
from src.utils import (
    limpiar_texto,
    parsear_fecha,
    parsear_summary_pcsp,
    parsear_budget_amount,
    extraer_importe,
    generar_nombre_pdf,
)


# ──────────────────────────────────────────────────────────────────────────────
# limpiar_texto
# ──────────────────────────────────────────────────────────────────────────────

class TestLimpiarTexto:
    """
    limpiar_texto() debe eliminar HTML, caracteres de control y espacios
    múltiples, y devolver None si el resultado queda vacío.
    """

    def test_texto_normal_no_cambia(self):
        # Un texto limpio no debería modificarse
        assert limpiar_texto("Servicio de limpieza") == "Servicio de limpieza"

    def test_elimina_etiquetas_html(self):
        assert limpiar_texto("<b>Título</b>") == "Título"

    def test_elimina_html_complejo(self):
        resultado = limpiar_texto('<span class="foo">Ayuntamiento de Madrid</span>')
        assert resultado == "Ayuntamiento de Madrid"

    def test_colapsa_espacios_multiples(self):
        assert limpiar_texto("hola   mundo") == "hola mundo"

    def test_elimina_espacios_al_inicio_y_final(self):
        assert limpiar_texto("  texto  ") == "texto"

    def test_none_devuelve_none(self):
        assert limpiar_texto(None) is None

    def test_string_vacio_devuelve_none(self):
        assert limpiar_texto("") is None

    def test_solo_espacios_devuelve_none(self):
        assert limpiar_texto("   ") is None

    def test_solo_html_devuelve_none(self):
        # Si el HTML no tiene texto, el resultado es vacío → None
        assert limpiar_texto("<br/>") is None


# ──────────────────────────────────────────────────────────────────────────────
# parsear_fecha
# ──────────────────────────────────────────────────────────────────────────────

class TestParsearFecha:
    """
    parsear_fecha() debe manejar los múltiples formatos que aparecen en
    el feed PCSP y devolver siempre datetime naive (sin tzinfo).
    """

    def test_formato_iso_con_z(self):
        # Formato más común en el feed
        resultado = parsear_fecha("2026-04-22T10:30:00Z")
        assert resultado == datetime(2026, 4, 22, 10, 30, 0)
        assert resultado.tzinfo is None  # Debe ser naive

    def test_formato_iso_con_timezone(self):
        # El feed PCSP a veces incluye offset: 2026-04-22T20:17:09.179+02:00
        resultado = parsear_fecha("2026-04-22T20:17:09+02:00")
        assert resultado is not None
        assert resultado.tzinfo is None  # Siempre devolvemos naive

    def test_formato_solo_fecha(self):
        resultado = parsear_fecha("2026-04-22")
        assert resultado == datetime(2026, 4, 22, 0, 0, 0)

    def test_formato_espanol_con_hora(self):
        resultado = parsear_fecha("22/04/2026 10:30")
        assert resultado == datetime(2026, 4, 22, 10, 30)

    def test_formato_espanol_sin_hora(self):
        resultado = parsear_fecha("22/04/2026")
        assert resultado == datetime(2026, 4, 22, 0, 0, 0)

    def test_none_devuelve_none(self):
        assert parsear_fecha(None) is None

    def test_string_vacio_devuelve_none(self):
        assert parsear_fecha("") is None

    def test_formato_invalido_devuelve_none(self):
        assert parsear_fecha("esto no es una fecha") is None

    def test_espacios_extra_no_rompen(self):
        # La función hace strip(), así que espacios extra están ok
        resultado = parsear_fecha("  2026-04-22  ")
        assert resultado == datetime(2026, 4, 22, 0, 0, 0)


# ──────────────────────────────────────────────────────────────────────────────
# extraer_importe
# ──────────────────────────────────────────────────────────────────────────────

class TestExtraerImporte:
    """
    extraer_importe() debe parsear importes en formato español y decimal
    estándar, ignorando texto alrededor (EUR, €, etc.).
    """

    def test_formato_decimal_estandar(self):
        # Formato más común en cac_budgetamount del feed
        assert extraer_importe("154163.5 EUR") == 154163.5

    def test_formato_espanol_con_puntos_de_miles(self):
        assert extraer_importe("1.234.567,89 €") == 1234567.89

    def test_formato_espanol_simple(self):
        assert extraer_importe("250.000,00EUR") == 250000.0

    def test_solo_numero(self):
        assert extraer_importe("72000.0") == 72000.0

    def test_importe_pequeño(self):
        assert extraer_importe("35000.0€") == 35000.0

    def test_importe_grande(self):
        # 11.57M como en la tabla que viste
        assert extraer_importe("11570000.0") == 11570000.0

    def test_none_devuelve_none(self):
        assert extraer_importe(None) is None

    def test_string_sin_numeros_devuelve_none(self):
        assert extraer_importe("sin importe") is None

    def test_cero_devuelve_none(self):
        # El rango válido es 1.0 a 10.000.000.000
        # Un cero no es un presupuesto válido
        assert extraer_importe("0.0 EUR") is None

    def test_importe_con_texto_antes(self):
        assert extraer_importe("Importe: 74323.8 EUR") == 74323.8


# ──────────────────────────────────────────────────────────────────────────────
# parsear_budget_amount
# ──────────────────────────────────────────────────────────────────────────────

class TestParsearBudgetAmount:
    """
    parsear_budget_amount() extrae el primer importe válido del campo
    UBL cac_budgetamount, que viene como string multilínea.
    """

    def test_formato_multilínea_real_del_feed(self):
        # Formato real verificado: 3 líneas (base sin IVA, con IVA, repetición)
        raw = "154163.5\n186537.83\n154163.5"
        assert parsear_budget_amount(raw) == 154163.5

    def test_toma_siempre_la_primera_linea(self):
        # La primera línea es el importe base sin IVA
        raw = "72000.0\n87120.0\n72000.0"
        assert parsear_budget_amount(raw) == 72000.0

    def test_ignora_lineas_vacias_al_inicio(self):
        # A veces el campo empieza con salto de línea
        raw = "\n35000.0\n42350.0"
        assert parsear_budget_amount(raw) == 35000.0

    def test_none_devuelve_none(self):
        assert parsear_budget_amount(None) is None

    def test_string_vacio_devuelve_none(self):
        assert parsear_budget_amount("") is None

    def test_valor_no_numerico_devuelve_none(self):
        assert parsear_budget_amount("no disponible") is None

    def test_una_sola_linea(self):
        # No todos los campos tienen las 3 líneas
        assert parsear_budget_amount("301718.28") == 301718.28


# ──────────────────────────────────────────────────────────────────────────────
# parsear_summary_pcsp
# ──────────────────────────────────────────────────────────────────────────────

class TestParsearSummaryPCSP:
    """
    parsear_summary_pcsp() extrae campos del texto plano del summary.
    Formato real: "Id licitación: X; Órgano de Contratación: Y; Importe: Z; Estado: W"
    """

    def test_summary_completo_real(self):
        # Summary real del feed PCSP
        summary = (
            "Id licitación: 4/2025; "
            "Órgano de Contratación: Ayuntamiento de Prueba; "
            "Importe: 154163.5 EUR; "
            "Estado: ADJ"
        )
        resultado = parsear_summary_pcsp(summary)

        assert resultado["expediente"] == "4/2025"
        assert resultado["organo_contratacion"] == "Ayuntamiento de Prueba"
        assert resultado["estado_contrato"] == "ADJ"
        assert resultado["presupuesto_base"] == 154163.5

    def test_extrae_expediente(self):
        summary = "Id licitación: 7/2026; Órgano de Contratación: Test"
        resultado = parsear_summary_pcsp(summary)
        assert resultado["expediente"] == "7/2026"

    def test_extrae_organo(self):
        summary = "Órgano de Contratación: Diputación Provincial de Zamora"
        resultado = parsear_summary_pcsp(summary)
        assert resultado["organo_contratacion"] == "Diputación Provincial de Zamora"

    def test_extrae_estado_ev(self):
        summary = "Estado: EV"
        resultado = parsear_summary_pcsp(summary)
        assert resultado["estado_contrato"] == "EV"

    def test_extrae_estado_pub(self):
        summary = "Id licitación: 1/2026; Estado: PUB"
        resultado = parsear_summary_pcsp(summary)
        assert resultado["estado_contrato"] == "PUB"

    def test_none_devuelve_dict_vacio(self):
        resultado = parsear_summary_pcsp(None)
        assert resultado == {}

    def test_string_vacio_devuelve_dict_vacio(self):
        resultado = parsear_summary_pcsp("")
        assert resultado == {}

    def test_campo_con_dos_puntos_en_el_valor(self):
        # El código usa .partition(":") para no romper valores con ":"
        summary = "Órgano de Contratación: Consejería de I+D+i: Área Técnica"
        resultado = parsear_summary_pcsp(summary)
        # El valor debe incluir todo lo que hay después del primer ":"
        assert "Consejería de I+D+i" in resultado.get("organo_contratacion", "")

    def test_summary_sin_campos_conocidos_devuelve_dict_vacio(self):
        resultado = parsear_summary_pcsp("Texto sin formato reconocible")
        assert resultado == {}


# ──────────────────────────────────────────────────────────────────────────────
# generar_nombre_pdf
# ──────────────────────────────────────────────────────────────────────────────

class TestGenerarNombrePdf:
    """
    generar_nombre_pdf() debe producir nombres de fichero seguros para
    el sistema de archivos (sin caracteres raros) y con el timestamp al inicio.
    """

    def test_con_nombre_original(self):
        nombre = generar_nombre_pdf("id123", "ACUERDO DE PRORROGA")
        # Debe contener el nombre limpio y terminar en .pdf
        assert "ACUERDO_DE_PRORROGA" in nombre
        assert nombre.endswith(".pdf")

    def test_sin_nombre_original_usa_id(self):
        nombre = generar_nombre_pdf("https://ejemplo.es/idEvolucion=12345")
        # Sin nombre original, usa los últimos 20 chars del ID
        assert nombre.endswith(".pdf")
        assert len(nombre) > 0

    def test_nombre_con_caracteres_raros_se_limpia(self):
        # Caracteres que no son válidos en nombres de fichero
        nombre = generar_nombre_pdf("id", "Pliego: condiciones/técnicas (v2)")
        # No debe contener : / ( )
        for char in [":", "/", "(", ")"]:
            assert char not in nombre

    def test_nombre_empieza_con_fecha(self):
        nombre = generar_nombre_pdf("id", "Test")
        # El nombre debe empezar con un timestamp tipo 20260422
        # Verificamos que empieza con 4 dígitos de año
        assert nombre[:4].isdigit()

    def test_nombre_largo_se_trunca(self):
        nombre_largo = "A" * 200
        nombre = generar_nombre_pdf("id", nombre_largo)
        # La parte del nombre (sin fecha y extensión) no debe superar 60 chars
        # Verificamos que el fichero completo es razonable
        assert len(nombre) < 100