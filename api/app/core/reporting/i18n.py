from __future__ import annotations

# Fixed report chrome (headers, labels, section titles, disclaimer) for the
# PDF and tax CSV — everything our own code renders, not RP2's own output
# (RP2's Spain plugin only ships Spanish templates; that raw ODS output is
# unaffected by this and stays in Spanish regardless of the setting here).
#
# Scope note: the dynamically-generated methodology warnings (e.g. "N
# events have no price yet", listing specific event types) stay in English
# regardless of language — they're built from application logic per report,
# not fixed strings, and fully localizing free-form interpolated lists is a
# separate piece of work. Everything else on the page is translated.

SUPPORTED_LANGUAGES = ("en", "es")
DEFAULT_LANGUAGE = "en"

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Crypto Tax Report",
        "disclaimer": (
            "This is a computed working document, not a filed tax return or professional advice. Review it "
            "with an accountant or tax adviser and verify it against the official rules in force for the "
            "selected country and tax year before relying on it."
        ),
        "report_identity": "Report identity",
        "taxpayer": "Taxpayer",
        "jurisdiction": "Jurisdiction",
        "tax_year": "Tax year",
        "accounting_method": "Accounting method",
        "currency": "Currency",
        "generated": "Generated",
        "summary": "Summary",
        "short_term": "Short-term gain/loss",
        "long_term": "Long-term gain/loss",
        "net_gain": "Net capital gain/loss",
        "total_income": "Total income",
        "total_fees": "Total fees",
        "assets": "Assets",
        "asset": "Asset",
        "acquired": "Acquired",
        "disposed": "Disposed",
        "held": "Held",
        "cost_basis_remaining": "Cost basis remaining",
        "no_assets": "No asset positions to report.",
        "disposals": "Disposals and gains/losses",
        "category": "Category",
        "term": "Term",
        "quantity": "Quantity",
        "proceeds": "Proceeds",
        "cost_basis": "Cost basis",
        "gain_loss": "Gain/loss",
        "no_disposals": "No disposals recorded for this tax year.",
        "income_section": "Income (staking, mining, airdrops, interest, ...)",
        "value": "Value",
        "no_income": "No separately-reportable income recorded for this tax year.",
        "pricing_methodology": "Pricing methodology",
        "pricing_text": (
            "Historical prices are sourced from CoinGecko's daily reference price in {currency}, cached "
            "locally once fetched. Each event's valuation carries its own provenance (provider, method, "
            "timestamp) in the underlying ledger - see the Full Ledger CSV export for the per-event detail."
        ),
        "methodology": "Methodology and known limitations",
        "no_warnings": "No warnings were raised while generating this report.",
        "source_info": "Source information",
        "source_text": (
            "This report is derived from the canonical ledger's raw, source-verified evidence (exchange APIs, "
            "blockchain explorers, or manually-entered records clearly marked as such). See the Evidence "
            "Archive export for the complete raw payloads and integrity hashes behind every figure above."
        ),
        "totals": "Totals",
        "working_document_header": "# Tax report - {country} {tax_year} - method {method} - currency {currency}",
        "working_document_note": "# Working document - verify against official rules for this jurisdiction and tax year.",
        "acquisitions": "Acquisitions",
        "cost_basis_established": "Cost basis",
        "no_acquisitions": "No acquisitions recorded for this tax year.",
        "fees_section": "Fees",
        "fees_folded_note": (
            "This engine folds fees into cost basis/proceeds rather than tracking them as a separate total - "
            "see the raw output files for the exact per-transaction figures."
        ),
        "no_fees": "No separately-tracked fees for this tax year.",
        "transfers_section": "Transfers (internal, non-taxable)",
        "from_account": "From",
        "to_account": "To",
        "date": "Date",
        "no_transfers": "No internal transfers linked for this tax year.",
        "corrections_section": "Manual corrections",
        "event": "Event",
        "field": "Field",
        "old_value": "Old value",
        "new_value": "New value",
        "changed_at": "Changed",
        "no_corrections": "No manual corrections were applied to events in this tax year.",
        "reconciliation_section": "Reconciliation status",
        "linked_transfers": "Linked internal transfers",
        "unresolved_issues": "Unresolved data-quality issues",
        "manual_events": "Manually-entered events",
        "schedule_section": "Detailed event schedule",
        "event_type": "Type",
        "counterparty": "Counterparty",
        "schedule_truncated": (
            "Showing the first {shown} of {total} events for this tax year - download the Full Ledger CSV for "
            "the complete itemized list."
        ),
        "no_events": "No events recorded for this tax year.",
        "accountant_title": "Accountant Report (Jurisdiction-Neutral)",
        "accountant_disclaimer": (
            "This is a jurisdiction-neutral summary of the canonical ledger - it does not apply any country's "
            "tax rules or compute a gain/loss figure. Use the country-specific Tax Report for a jurisdiction's "
            "computed result. Review this with an accountant or tax adviser."
        ),
        "ledger_overview": "Ledger overview",
        "total_events": "Total events",
        "date_range": "Date range",
        "accounts_section": "Accounts",
        "name": "Name",
        "kind": "Kind",
        "connector": "Connector",
        "accountant_pricing_text": (
            "Historical prices are sourced from CoinGecko's daily reference price in each event's own quote "
            "currency, cached locally once fetched. Each event's valuation carries its own provenance "
            "(provider, method, timestamp) - see the Full Ledger CSV export for the per-event detail."
        ),
    },
    "es": {
        "title": "Informe Fiscal de Criptomonedas",
        "disclaimer": (
            "Este es un documento de trabajo calculado, no una declaración de impuestos presentada ni "
            "asesoramiento profesional. Revísalo con un asesor fiscal y verifícalo contra las normas oficiales "
            "vigentes para el país y el ejercicio fiscal seleccionados antes de basarte en él."
        ),
        "report_identity": "Identidad del informe",
        "taxpayer": "Contribuyente",
        "jurisdiction": "Jurisdicción",
        "tax_year": "Ejercicio fiscal",
        "accounting_method": "Método contable",
        "currency": "Moneda",
        "generated": "Generado",
        "summary": "Resumen",
        "short_term": "Ganancia/pérdida a corto plazo",
        "long_term": "Ganancia/pérdida a largo plazo",
        "net_gain": "Ganancia/pérdida neta",
        "total_income": "Ingresos totales",
        "total_fees": "Comisiones totales",
        "assets": "Activos",
        "asset": "Activo",
        "acquired": "Adquirido",
        "disposed": "Dispuesto",
        "held": "En cartera",
        "cost_basis_remaining": "Base de coste restante",
        "no_assets": "No hay posiciones de activos que declarar.",
        "disposals": "Disposiciones y ganancias/pérdidas",
        "category": "Categoría",
        "term": "Plazo",
        "quantity": "Cantidad",
        "proceeds": "Importe obtenido",
        "cost_basis": "Base de coste",
        "gain_loss": "Ganancia/pérdida",
        "no_disposals": "No se registraron disposiciones en este ejercicio fiscal.",
        "income_section": "Ingresos (staking, minería, airdrops, intereses, ...)",
        "value": "Valor",
        "no_income": "No se registraron ingresos declarables por separado en este ejercicio fiscal.",
        "pricing_methodology": "Metodología de precios",
        "pricing_text": (
            "Los precios históricos se obtienen del precio de referencia diario de CoinGecko en {currency}, "
            "guardados localmente una vez consultados. La valoración de cada evento conserva su propia "
            "procedencia (proveedor, método, marca temporal) en el libro mayor subyacente - consulta la "
            "exportación CSV del libro completo para el detalle de cada evento."
        ),
        "methodology": "Metodología y limitaciones conocidas",
        "no_warnings": "No se generaron avisos al elaborar este informe.",
        "source_info": "Información de origen",
        "source_text": (
            "Este informe se deriva de la evidencia bruta y verificada del libro mayor canónico (APIs de "
            "exchanges, exploradores de blockchain, o registros introducidos manualmente y claramente "
            "marcados como tales). Consulta la exportación del archivo de evidencias para los payloads "
            "brutos completos y los hashes de integridad detrás de cada cifra anterior."
        ),
        "totals": "Totales",
        "working_document_header": "# Informe fiscal - {country} {tax_year} - método {method} - moneda {currency}",
        "working_document_note": "# Documento de trabajo - verificar contra las normas oficiales de esta jurisdicción y ejercicio fiscal.",
        "acquisitions": "Adquisiciones",
        "cost_basis_established": "Base de coste",
        "no_acquisitions": "No se registraron adquisiciones en este ejercicio fiscal.",
        "fees_section": "Comisiones",
        "fees_folded_note": (
            "Este motor incorpora las comisiones en la base de coste/importe obtenido en lugar de "
            "contabilizarlas por separado - consulta los archivos de salida originales para las cifras exactas "
            "por transacción."
        ),
        "no_fees": "No hay comisiones registradas por separado en este ejercicio fiscal.",
        "transfers_section": "Transferencias (internas, no sujetas a impuesto)",
        "from_account": "Origen",
        "to_account": "Destino",
        "date": "Fecha",
        "no_transfers": "No se vincularon transferencias internas en este ejercicio fiscal.",
        "corrections_section": "Correcciones manuales",
        "event": "Evento",
        "field": "Campo",
        "old_value": "Valor anterior",
        "new_value": "Valor nuevo",
        "changed_at": "Modificado",
        "no_corrections": "No se aplicaron correcciones manuales a eventos de este ejercicio fiscal.",
        "reconciliation_section": "Estado de conciliación",
        "linked_transfers": "Transferencias internas vinculadas",
        "unresolved_issues": "Incidencias de calidad de datos sin resolver",
        "manual_events": "Eventos introducidos manualmente",
        "schedule_section": "Detalle de eventos",
        "event_type": "Tipo",
        "counterparty": "Contraparte",
        "schedule_truncated": (
            "Mostrando los primeros {shown} de {total} eventos de este ejercicio fiscal - descarga el CSV del "
            "libro completo para la lista detallada completa."
        ),
        "no_events": "No se registraron eventos en este ejercicio fiscal.",
        "accountant_title": "Informe para Asesoría (Neutral por Jurisdicción)",
        "accountant_disclaimer": (
            "Este es un resumen neutral por jurisdicción del libro mayor canónico - no aplica las normas "
            "fiscales de ningún país ni calcula una cifra de ganancia/pérdida. Usa el Informe Fiscal específico "
            "de un país para un resultado calculado por jurisdicción. Revísalo con un asesor fiscal."
        ),
        "ledger_overview": "Resumen del libro mayor",
        "total_events": "Eventos totales",
        "date_range": "Rango de fechas",
        "accounts_section": "Cuentas",
        "name": "Nombre",
        "kind": "Tipo",
        "connector": "Conector",
        "accountant_pricing_text": (
            "Los precios históricos se obtienen del precio de referencia diario de CoinGecko en la moneda de "
            "cotización propia de cada evento, guardados localmente una vez consultados. La valoración de cada "
            "evento conserva su propia procedencia (proveedor, método, marca temporal) - consulta la "
            "exportación CSV del libro completo para el detalle de cada evento."
        ),
    },
}


def t(language: str, key: str, **kwargs) -> str:
    strings = _STRINGS.get(language) or _STRINGS[DEFAULT_LANGUAGE]
    template = strings.get(key) or _STRINGS[DEFAULT_LANGUAGE].get(key, key)
    return template.format(**kwargs) if kwargs else template
