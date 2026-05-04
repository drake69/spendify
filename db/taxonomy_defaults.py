"""Built-in taxonomy templates per language.

Each language entry has:
  - label: human-readable name shown in the onboarding UI
  - expenses: list of {category, subcategories[]}
  - income:   list of {category, subcategories[]}

Adding a new language: add an entry to TAXONOMY_DEFAULTS and re-run the app.
The _migrate_add_taxonomy_default() migration is idempotent via INSERT OR IGNORE.
"""
from __future__ import annotations

TAXONOMY_DEFAULTS: dict[str, dict] = {

    # ── Italiano ──────────────────────────────────────────────────────────────
    "it": {
        "label": "Italiano",
        "expenses": [
            {"category": "Casa", "subcategories": [
                "Mutuo / Affitto", "Condominio", "Gas", "Energia elettrica",
                "Acqua", "Spazzatura (TARI)", "IMU / Tasse sulla casa",
                "Manutenzione e riparazioni", "Arredamento", "Elettrodomestici",
                "Altro casa",
            ]},
            {"category": "Alimentari", "subcategories": [
                "Spesa supermercato", "Mercato / freschi",
                "Macelleria / pescheria", "Altro alimentari",
            ]},
            {"category": "Ristorazione", "subcategories": [
                "Ristorante", "Bar / caffè", "Asporto / delivery",
            ]},
            {"category": "Trasporti", "subcategories": [
                "Carburante", "Assicurazione auto", "Bollo auto",
                "Manutenzione auto", "Parcheggio / ZTL",
                "Trasporto pubblico", "Taxi / ride-sharing", "Altro trasporti",
            ]},
            {"category": "Salute", "subcategories": [
                "Medico di base / specialista", "Farmaci",
                "Analisi / diagnostica", "Dentista", "Ottico", "Altro salute",
            ]},
            {"category": "Istruzione", "subcategories": [
                "Rette scolastiche", "Libri e materiale",
                "Corsi e formazione", "Altro istruzione",
            ]},
            {"category": "Abbigliamento", "subcategories": [
                "Abbigliamento adulti", "Abbigliamento bambini",
                "Calzature", "Altro abbigliamento",
            ]},
            {"category": "Comunicazioni", "subcategories": [
                "Telefonia mobile", "Internet / fibra", "Telefonia fissa",
            ]},
            {"category": "Svago e tempo libero", "subcategories": [
                "Sport e palestra", "Cinema / teatro / eventi",
                "Streaming / abbonamenti digitali", "Viaggi e vacanze",
                "Hobby", "Libri / riviste", "Altro svago",
            ]},
            {"category": "Animali domestici", "subcategories": [
                "Cibo", "Veterinario", "Accessori",
            ]},
            {"category": "Finanza e assicurazioni", "subcategories": [
                "Assicurazione vita", "Assicurazione casa", "Polizze varie",
                "Commissioni bancarie", "Investimenti / risparmio",
                "Fondo pensione",
            ]},
            {"category": "Cura personale", "subcategories": [
                "Parrucchiere / barbiere", "Cosmetici e igiene",
                "Centro estetico / SPA",
            ]},
            {"category": "Tasse e tributi", "subcategories": [
                "IRPEF / F24", "Imposte varie", "Sanzioni / more",
            ]},
            {"category": "Regali e donazioni", "subcategories": [
                "Regali", "Donazioni / beneficenza",
            ]},
            {"category": "Altro", "is_fallback": True, "subcategories": [
                "Spese non classificate",
            ]},
        ],
        "income": [
            {"category": "Lavoro dipendente", "subcategories": [
                "Stipendio", "Tredicesima / quattordicesima",
                "Bonus e premi", "Rimborso spese lavorative",
            ]},
            {"category": "Lavoro autonomo", "subcategories": [
                "Fattura / parcella", "Collaborazione occasionale",
                "Diritti d'autore / royalties",
            ]},
            {"category": "Rendite finanziarie", "subcategories": [
                "Dividendi", "Interessi attivi", "Plusvalenze",
                "Cedole obbligazionarie",
            ]},
            {"category": "Rendite immobiliari", "subcategories": [
                "Affitto percepito", "Vendita immobile",
            ]},
            {"category": "Trasferimenti e rimborsi", "subcategories": [
                "Rimborso generico", "Storno / rettifica bancaria",
                "Giroconto entrata", "Indennizzo / risarcimento",
            ]},
            {"category": "Prestazioni sociali", "subcategories": [
                "Pensione / rendita", "Sussidio / bonus statale",
                "Indennità (NASpI, maternità, ecc.)",
            ]},
            {"category": "Altro entrate", "is_fallback": True, "subcategories": [
                "Vendita beni usati", "Regalo ricevuto",
                "Entrate non classificate",
            ]},
        ],
    },

    # ── English ───────────────────────────────────────────────────────────────
    "en": {
        "label": "English",
        "expenses": [
            {"category": "Housing", "subcategories": [
                "Rent / Mortgage", "Condo fees", "Gas", "Electricity",
                "Water", "Waste collection", "Property tax",
                "Maintenance & repairs", "Furnishings", "Appliances",
                "Other housing",
            ]},
            {"category": "Groceries", "subcategories": [
                "Supermarket", "Fresh market", "Butcher / Fishmonger",
                "Other groceries",
            ]},
            {"category": "Dining out", "subcategories": [
                "Restaurant", "Bar / Café", "Takeaway / Delivery",
            ]},
            {"category": "Transport", "subcategories": [
                "Fuel", "Car insurance", "Vehicle tax", "Car maintenance",
                "Parking / Tolls", "Public transport", "Taxi / Ride-sharing",
                "Other transport",
            ]},
            {"category": "Health", "subcategories": [
                "GP / Specialist", "Medication", "Tests / Lab work",
                "Dentist", "Optician", "Other health",
            ]},
            {"category": "Education", "subcategories": [
                "School fees", "Books & materials", "Courses & training",
                "Other education",
            ]},
            {"category": "Clothing", "subcategories": [
                "Adult clothing", "Children's clothing", "Footwear",
                "Other clothing",
            ]},
            {"category": "Communications", "subcategories": [
                "Mobile phone", "Internet / Broadband", "Landline",
            ]},
            {"category": "Entertainment", "subcategories": [
                "Sport & gym", "Cinema / Theatre / Events",
                "Streaming & subscriptions", "Travel & holidays",
                "Hobbies", "Books & magazines", "Other entertainment",
            ]},
            {"category": "Pets", "subcategories": [
                "Food", "Vet", "Accessories",
            ]},
            {"category": "Finance & insurance", "subcategories": [
                "Life insurance", "Home insurance", "Other insurance",
                "Bank charges", "Investments / Savings", "Pension fund",
            ]},
            {"category": "Personal care", "subcategories": [
                "Hairdresser / Barber", "Cosmetics & hygiene",
                "Beauty salon / SPA",
            ]},
            {"category": "Taxes", "subcategories": [
                "Income tax", "Other taxes", "Fines & penalties",
            ]},
            {"category": "Gifts & donations", "subcategories": [
                "Gifts", "Donations / Charity",
            ]},
            {"category": "Other", "is_fallback": True, "subcategories": [
                "Unclassified expenses",
            ]},
        ],
        "income": [
            {"category": "Employment", "subcategories": [
                "Salary", "Holiday / Christmas bonus",
                "Bonuses & awards", "Work reimbursements",
            ]},
            {"category": "Self-employment", "subcategories": [
                "Invoice / Fees", "Occasional work", "Royalties",
            ]},
            {"category": "Financial income", "subcategories": [
                "Dividends", "Interest income", "Capital gains",
                "Bond coupons",
            ]},
            {"category": "Property income", "subcategories": [
                "Rental income", "Property sale",
            ]},
            {"category": "Transfers & refunds", "subcategories": [
                "Generic refund", "Bank reversal", "Transfer in",
                "Compensation",
            ]},
            {"category": "Social benefits", "subcategories": [
                "Pension / Annuity", "Government subsidy",
                "Allowance (unemployment, maternity, etc.)",
            ]},
            {"category": "Other income", "is_fallback": True, "subcategories": [
                "Sale of used goods", "Gift received",
                "Unclassified income",
            ]},
        ],
    },

    # ── Français ──────────────────────────────────────────────────────────────
    "fr": {
        "label": "Français",
        "expenses": [
            {"category": "Logement", "subcategories": [
                "Loyer / Hypothèque", "Charges de copropriété", "Gaz",
                "Électricité", "Eau", "Ordures ménagères", "Taxe foncière",
                "Entretien & réparations", "Mobilier", "Électroménager",
                "Autre logement",
            ]},
            {"category": "Alimentation", "subcategories": [
                "Supermarché", "Marché / produits frais",
                "Boucherie / poissonnerie", "Autre alimentation",
            ]},
            {"category": "Restauration", "subcategories": [
                "Restaurant", "Bar / café", "Vente à emporter / livraison",
            ]},
            {"category": "Transports", "subcategories": [
                "Carburant", "Assurance auto", "Taxe automobile",
                "Entretien auto", "Parking / péages",
                "Transports en commun", "Taxi / VTC", "Autres transports",
            ]},
            {"category": "Santé", "subcategories": [
                "Médecin généraliste / spécialiste", "Médicaments",
                "Analyses / imagerie", "Dentiste", "Opticien",
                "Autre santé",
            ]},
            {"category": "Éducation", "subcategories": [
                "Frais de scolarité", "Livres & fournitures",
                "Cours & formations", "Autre éducation",
            ]},
            {"category": "Habillement", "subcategories": [
                "Vêtements adultes", "Vêtements enfants",
                "Chaussures", "Autre habillement",
            ]},
            {"category": "Communications", "subcategories": [
                "Téléphonie mobile", "Internet / fibre", "Téléphonie fixe",
            ]},
            {"category": "Loisirs", "subcategories": [
                "Sport & salle de sport", "Cinéma / théâtre / événements",
                "Streaming & abonnements numériques", "Voyages & vacances",
                "Loisirs créatifs", "Livres & revues", "Autres loisirs",
            ]},
            {"category": "Animaux", "subcategories": [
                "Nourriture", "Vétérinaire", "Accessoires",
            ]},
            {"category": "Finances & assurances", "subcategories": [
                "Assurance vie", "Assurance habitation",
                "Autres assurances", "Frais bancaires",
                "Investissements / épargne", "Fonds de retraite",
            ]},
            {"category": "Soins personnels", "subcategories": [
                "Coiffeur / barbier", "Cosmétiques & hygiène",
                "Institut de beauté / SPA",
            ]},
            {"category": "Impôts", "subcategories": [
                "Impôt sur le revenu", "Autres impôts",
                "Amendes & pénalités",
            ]},
            {"category": "Cadeaux & dons", "subcategories": [
                "Cadeaux", "Dons / associations",
            ]},
            {"category": "Autre", "is_fallback": True, "subcategories": [
                "Dépenses non classifiées",
            ]},
        ],
        "income": [
            {"category": "Salariat", "subcategories": [
                "Salaire", "Prime de fin d'année / de vacances",
                "Primes & récompenses", "Remboursements professionnels",
            ]},
            {"category": "Travail indépendant", "subcategories": [
                "Facture / honoraires", "Travail occasionnel",
                "Droits d'auteur / royalties",
            ]},
            {"category": "Revenus financiers", "subcategories": [
                "Dividendes", "Intérêts", "Plus-values",
                "Coupons obligataires",
            ]},
            {"category": "Revenus immobiliers", "subcategories": [
                "Loyer perçu", "Vente immobilière",
            ]},
            {"category": "Virements & remboursements", "subcategories": [
                "Remboursement générique", "Avoir bancaire",
                "Virement entrant", "Indemnisation",
            ]},
            {"category": "Prestations sociales", "subcategories": [
                "Retraite / rente", "Allocation / aide de l'État",
                "Indemnité (chômage, maternité, etc.)",
            ]},
            {"category": "Autres revenus", "is_fallback": True, "subcategories": [
                "Vente de biens d'occasion", "Cadeau reçu",
                "Revenus non classifiés",
            ]},
        ],
    },

    # ── Deutsch ───────────────────────────────────────────────────────────────
    "de": {
        "label": "Deutsch",
        "expenses": [
            {"category": "Wohnen", "subcategories": [
                "Miete / Hypothek", "Nebenkosten", "Gas", "Strom",
                "Wasser", "Müllabfuhr", "Grundsteuer",
                "Instandhaltung & Reparaturen", "Möbel",
                "Haushaltsgeräte", "Sonstiges Wohnen",
            ]},
            {"category": "Lebensmittel", "subcategories": [
                "Supermarkt", "Markt / Frischprodukte",
                "Metzger / Fischhändler", "Sonstige Lebensmittel",
            ]},
            {"category": "Gastronomie", "subcategories": [
                "Restaurant", "Bar / Café", "Takeaway / Lieferservice",
            ]},
            {"category": "Transport", "subcategories": [
                "Kraftstoff", "Kfz-Versicherung", "Kfz-Steuer",
                "Kfz-Wartung", "Parken / Maut",
                "Öffentliche Verkehrsmittel", "Taxi / Ridesharing",
                "Sonstiger Transport",
            ]},
            {"category": "Gesundheit", "subcategories": [
                "Hausarzt / Facharzt", "Medikamente",
                "Untersuchungen / Diagnostik", "Zahnarzt",
                "Optiker", "Sonstiges Gesundheit",
            ]},
            {"category": "Bildung", "subcategories": [
                "Schulgebühren", "Bücher & Material",
                "Kurse & Weiterbildung", "Sonstiges Bildung",
            ]},
            {"category": "Bekleidung", "subcategories": [
                "Erwachsenenbekleidung", "Kinderbekleidung",
                "Schuhe", "Sonstiges Bekleidung",
            ]},
            {"category": "Kommunikation", "subcategories": [
                "Mobiltelefon", "Internet / Glasfaser", "Festnetz",
            ]},
            {"category": "Freizeit", "subcategories": [
                "Sport & Fitnessstudio", "Kino / Theater / Veranstaltungen",
                "Streaming & digitale Abonnements", "Reisen & Urlaub",
                "Hobbys", "Bücher & Zeitschriften", "Sonstige Freizeit",
            ]},
            {"category": "Haustiere", "subcategories": [
                "Futter", "Tierarzt", "Zubehör",
            ]},
            {"category": "Finanzen & Versicherungen", "subcategories": [
                "Lebensversicherung", "Hausratversicherung",
                "Sonstige Versicherungen", "Bankgebühren",
                "Geldanlagen / Sparen", "Rentenversicherung",
            ]},
            {"category": "Körperpflege", "subcategories": [
                "Friseur / Barbier", "Kosmetik & Hygiene",
                "Beauty-Salon / SPA",
            ]},
            {"category": "Steuern", "subcategories": [
                "Einkommensteuer", "Sonstige Steuern",
                "Bußgelder & Strafen",
            ]},
            {"category": "Geschenke & Spenden", "subcategories": [
                "Geschenke", "Spenden / Charity",
            ]},
            {"category": "Sonstiges", "is_fallback": True, "subcategories": [
                "Nicht klassifizierte Ausgaben",
            ]},
        ],
        "income": [
            {"category": "Angestellt", "subcategories": [
                "Gehalt", "Urlaubsgeld / Weihnachtsgeld",
                "Boni & Prämien", "Kostenerstattungen",
            ]},
            {"category": "Selbstständig", "subcategories": [
                "Rechnung / Honorar", "Gelegenheitsarbeit",
                "Urheberrechte / Lizenzgebühren",
            ]},
            {"category": "Kapitalerträge", "subcategories": [
                "Dividenden", "Zinserträge", "Kursgewinne",
                "Anleihekupons",
            ]},
            {"category": "Mieteinnahmen", "subcategories": [
                "Erhaltene Miete", "Immobilienverkauf",
            ]},
            {"category": "Überweisungen & Erstattungen", "subcategories": [
                "Allgemeine Erstattung", "Bankgutschrift",
                "Eingehende Überweisung", "Entschädigung",
            ]},
            {"category": "Sozialleistungen", "subcategories": [
                "Rente / Altersrente", "Staatliche Förderung",
                "Zuwendungen (Arbeitslosengeld, Mutterschaft, etc.)",
            ]},
            {"category": "Sonstige Einnahmen", "is_fallback": True, "subcategories": [
                "Verkauf gebrauchter Waren", "Erhaltenes Geschenk",
                "Nicht klassifizierte Einnahmen",
            ]},
        ],
    },

    # ── Español ───────────────────────────────────────────────────────────────
    "es": {
        "label": "Español",
        "expenses": [
            {"category": "Vivienda", "subcategories": [
                "Alquiler / Hipoteca", "Comunidad de propietarios", "Gas",
                "Electricidad", "Agua", "Basuras", "IBI / Impuestos vivienda",
                "Mantenimiento & reparaciones", "Mobiliario",
                "Electrodomésticos", "Otros vivienda",
            ]},
            {"category": "Alimentación", "subcategories": [
                "Supermercado", "Mercado / frescos",
                "Carnicería / pescadería", "Otros alimentación",
            ]},
            {"category": "Restauración", "subcategories": [
                "Restaurante", "Bar / cafetería", "Comida para llevar / delivery",
            ]},
            {"category": "Transporte", "subcategories": [
                "Combustible", "Seguro de coche", "Impuesto de circulación",
                "Mantenimiento coche", "Aparcamiento / peajes",
                "Transporte público", "Taxi / ride-sharing",
                "Otros transporte",
            ]},
            {"category": "Salud", "subcategories": [
                "Médico de cabecera / especialista", "Medicamentos",
                "Análisis / diagnóstico", "Dentista", "Óptico",
                "Otros salud",
            ]},
            {"category": "Educación", "subcategories": [
                "Tasas escolares", "Libros y material",
                "Cursos y formación", "Otros educación",
            ]},
            {"category": "Ropa", "subcategories": [
                "Ropa adultos", "Ropa niños", "Calzado", "Otros ropa",
            ]},
            {"category": "Comunicaciones", "subcategories": [
                "Telefonía móvil", "Internet / fibra", "Telefonía fija",
            ]},
            {"category": "Ocio y tiempo libre", "subcategories": [
                "Deporte y gimnasio", "Cine / teatro / eventos",
                "Streaming / suscripciones digitales", "Viajes y vacaciones",
                "Hobbies", "Libros / revistas", "Otros ocio",
            ]},
            {"category": "Mascotas", "subcategories": [
                "Comida", "Veterinario", "Accesorios",
            ]},
            {"category": "Finanzas y seguros", "subcategories": [
                "Seguro de vida", "Seguro de hogar", "Otros seguros",
                "Comisiones bancarias", "Inversiones / ahorro",
                "Plan de pensiones",
            ]},
            {"category": "Cuidado personal", "subcategories": [
                "Peluquería / barbería", "Cosméticos e higiene",
                "Centro de estética / SPA",
            ]},
            {"category": "Impuestos", "subcategories": [
                "IRPF / declaración", "Otros impuestos",
                "Sanciones / multas",
            ]},
            {"category": "Regalos y donaciones", "subcategories": [
                "Regalos", "Donaciones / ONG",
            ]},
            {"category": "Otros", "is_fallback": True, "subcategories": [
                "Gastos no clasificados",
            ]},
        ],
        "income": [
            {"category": "Trabajo por cuenta ajena", "subcategories": [
                "Salario", "Paga extra / navidad",
                "Bonus y premios", "Reembolso gastos laborales",
            ]},
            {"category": "Trabajo por cuenta propia", "subcategories": [
                "Factura / honorarios", "Colaboración ocasional",
                "Derechos de autor / royalties",
            ]},
            {"category": "Rentas financieras", "subcategories": [
                "Dividendos", "Intereses", "Plusvalías",
                "Cupones de bonos",
            ]},
            {"category": "Rentas inmobiliarias", "subcategories": [
                "Alquiler percibido", "Venta de inmueble",
            ]},
            {"category": "Transferencias y reembolsos", "subcategories": [
                "Reembolso genérico", "Devolución bancaria",
                "Traspaso recibido", "Indemnización",
            ]},
            {"category": "Prestaciones sociales", "subcategories": [
                "Pensión / renta", "Subsidio / ayuda estatal",
                "Prestación (desempleo, maternidad, etc.)",
            ]},
            {"category": "Otros ingresos", "is_fallback": True, "subcategories": [
                "Venta de bienes usados", "Regalo recibido",
                "Ingresos no clasificados",
            ]},
        ],
    },
}

# Ordered list for UI display (onboarding language picker)
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    (code, data["label"])
    for code, data in TAXONOMY_DEFAULTS.items()
]
