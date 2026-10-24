"""
Costruisce public/data/orario_completo.json incrociando:
  - data/Orario-Classi.pdf   (fonte primaria: materia, docente, compresenza, aula/lab)
  - data/Orario-Docenti.pdf  (fonte primaria: disposizioni, orario per docente)
Le incongruenze tra i due PDF NON vengono corrette in silenzio:
finiscono in data/report_anomalie.json.
"""
import json
import os
import re
import unicodedata
from collections import defaultdict

import pdfplumber

PDF_CLASSI = "data/Orario-Classi.pdf"
PDF_DOCENTI = "data/Orario-Docenti.pdf"
PDF_LABORATORI = "data/Orario-Laboratori.pdf"
OUT_JSON = "public/data/orario_completo.json"
OUT_REPORT = "data/report_anomalie.json"

GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato"]
RE_TIME = re.compile(r"^\d{1,2}:\d{2}$")

# Fasce orarie reali -> numero d'ora. Gli intervalli (10:35, 13:35) non sono ore.
ORE = {"7:50": 1, "8:45": 2, "9:40": 3, "10:50": 4,
       "11:45": 5, "12:40": 6, "13:55": 7, "14:50": 8}
INTERVALLI = {"10:35", "13:35"}
ORE_INFO = [{"ora": n, "inizio": t} for t, n in ORE.items()]

# Aule "speciali": le aule ordinarie delle classi NON compaiono nei PDF,
# quindi le aule libere si calcolano solo per queste.
RE_AULA_SPECIALE = re.compile(r"^(LAB\b|PALESTRA)", re.I)


# ---------------------------------------------------------------- normalizzazione
def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm_giorno(label):
    """'Lunedì 5' -> 'lunedi'."""
    w = strip_accents(label.strip().split()[0]).lower()
    return w if w in GIORNI else None


def norm_classe(raw):
    """'3 BSA', '[3 BSA]', '3bsa', 'CLASSE 3BSA' -> '3BSA'."""
    s = re.sub(r"^\s*CLASSE\s+", "", raw or "", flags=re.I)
    return re.sub(r"[^0-9A-Za-z]", "", s).upper()


def norm_persona(raw):
    """Chiave di confronto per i nomi (i PDF troncano a 20 caratteri)."""
    s = strip_accents(raw or "").upper()
    return re.sub(r"[^A-Z0-9 ]", "", s).strip()


def persona_id(raw):
    return re.sub(r"\s+", "_", norm_persona(raw))


def norm_aula(raw):
    s = re.sub(r"\s+", " ", (raw or "").strip()).upper()
    return s


def cell(c):
    return re.sub(r"\s+", " ", c.replace("\n", " ")).strip() if c else ""


def parse_griglia_classi():
    """Legge data/GRIGLIA CLASSI 26 27.csv o data/*.csv e restituisce {classe: aula_ordinaria}"""
    csv_files = [f for f in os.listdir("data") if f.endswith(".csv")]
    if not csv_files:
        return {}
    
    mapping = {}
    csv_path = os.path.join("data", csv_files[0])
    import csv
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    def add_pair(aula, classe):
        aula = norm_aula(aula)
        raw_c = (classe or "").strip()
        if not aula or not raw_c or aula in (
            "LABORATORIO", "INFO 1", "INFO2", "INFO 2", "AULA", "PLESSO STORICO", "PLESSO NUOVO"
        ) or raw_c.upper() in (
            "CLASSE", "BANCHI", "POLIVALENTE", "LABORATORIO DI INFORMATICA", "CANTINA", "INFO 2",
            "CHIMICA 1", "CHIMICA 2", "FITOPATOLOGIA", "BIOLOGIA", "ENOLOGIA"
        ):
            return
        for part in raw_c.split("/"):
            c = norm_classe(part)
            if c:
                mapping[c] = aula
                if c.endswith("AT") and not c.endswith("AET"):
                    mapping[c.replace("AT", "AET")] = aula

    for row in rows:
        if len(row) >= 3:
            add_pair(row[0], row[1])
        if len(row) >= 7:
            add_pair(row[4], row[5])

    if "3PT" in mapping and "3PTA" not in mapping:
        mapping["3PTA"] = mapping["3PT"]

    return mapping



# ---------------------------------------------------------------- estrazione
def pages_tables(pdf_path):
    """Yield (testo_pagina, tabelle) con dedupe dei caratteri del finto grassetto."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            clean = page.dedupe_chars(tolerance=1)
            yield clean.extract_text() or "", clean.extract_tables()


def parse_classi(report):
    """-> {classe: {giorno: {ora: slot}}}"""
    out = {}
    for _, tables in pages_tables(PDF_CLASSI):
        for tb in tables:
            if not tb or len(tb) < 2:
                continue
            
            # Formato Spaggiari REV_01 (Tabella con 'CLASSE', 'Ora', 'Lunedì', ...)
            h0 = [cell(c) for c in tb[0]] if tb and tb[0] else []
            if len(h0) >= 3 and h0[0].upper() == "CLASSE" and "ORA" in h0[1].upper():
                classe = norm_classe(tb[1][0])
                giorni = [norm_giorno(h) if h else None for h in h0[2:]]
                sched = out.setdefault(classe, {g: {} for g in GIORNI})
                for row in tb[1:]:
                    ora_str = cell(row[1])
                    if ora_str in INTERVALLI or ora_str not in ORE:
                        continue
                    ora_num = ORE[ora_str]
                    materie = [cell(c) for c in row[2:]]
                    for i, g in enumerate(giorni):
                        if not g or i >= len(materie):
                            continue
                        materia = materie[i]
                        if not materia or materia.lower() == "intervallo":
                            continue
                        sched[g][ora_num] = {
                            "materia": materia,
                            "docenti": [],
                            "aula": None
                        }
                continue

            # Formato Legacy / Tabellare classico Spaggiari
            if len(tb) < 3 or not tb[0][1] or not tb[0][1].upper().startswith("CLASSE"):
                report["classi_tabelle_ignorate"].append(cell(tb[0][1] if tb and len(tb[0]) > 1 else ""))
                continue
            classe = norm_classe(tb[0][1])
            giorni = [norm_giorno(h) if h else None for h in tb[1][1:]]
            sched = out.setdefault(classe, {g: {} for g in GIORNI})
            cur_t, slots = None, {}
            for row in tb[2:]:
                tag = cell(row[0])
                if RE_TIME.match(tag):
                    cur_t = tag
                    slots[cur_t] = {"materia": [cell(c) for c in row[1:]],
                                    "docente": [""] * len(giorni),
                                    "compr": [""] * len(giorni),
                                    "aula": [""] * len(giorni)}
                elif cur_t is None:
                    continue
                elif tag == "":
                    slots[cur_t]["docente"] = [cell(c) for c in row[1:]]
                elif tag == "Compr":
                    slots[cur_t]["compr"] = [cell(c) for c in row[1:]]
                elif tag == "Labor":
                    slots[cur_t]["aula"] = [cell(c) for c in row[1:]]
                else:
                    report["classi_righe_sconosciute"].append({"classe": classe, "tag": tag})
            for t, s in slots.items():
                if t in INTERVALLI or t not in ORE:
                    continue
                for i, g in enumerate(giorni):
                    materia = s["materia"][i] if i < len(s["materia"]) else ""
                    if g is None or not materia or materia.lower() == "intervallo":
                        continue
                    docente = s["docente"][i]
                    compr = [c.strip() for c in re.split(r"[\n;/]", s["compr"][i]) if c.strip()] if s["compr"][i] else []
                    aula = norm_aula(s["aula"][i])
                    if not docente:
                        report["classi_slot_senza_docente"].append(f"{classe} {g} ora {ORE[t]} ({materia})")
                    sched[g][ORE[t]] = {"materia": materia, "docenti": ([docente] if docente else []) + compr,
                                        "aula": aula or None}
    return out


RE_CELLA_DOC = re.compile(r"^\[?\s*(\d)\s*([A-Za-z]+)\b\]?\s*(.*)$")


def parse_docenti(report):
    """-> {nome_docente: {giorno: {ora: {tipo, classe, aula}}}}, periodo"""
    out, periodo = {}, None
    for text, tables in pages_tables(PDF_DOCENTI):
        if periodo is None:
            m = re.search(r"(?:ORARIO\s+)?(DAL\s+\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}\s*-\s*\d{1,2}\s+[a-z]+\s+\d{4})", text, re.I)
            periodo = m.group(1).title() if m else None
        for tb in tables:
            if len(tb) < 2 or cell(tb[0][0]).upper() != "DOCENTE":
                report["docenti_tabelle_ignorate"].append(cell(tb[0][0]) if tb else "")
                continue
            nome = cell(tb[1][0])
            giorni = [norm_giorno(h) if h else None for h in tb[0][2:]]
            sched = out.setdefault(nome, {g: {} for g in GIORNI})
            for row in tb[1:]:
                t = cell(row[1])
                if t in INTERVALLI or t not in ORE:
                    continue
                for i, g in enumerate(giorni):
                    txt = cell(row[2 + i]) if 2 + i < len(row) else ""
                    if g is None or not txt or txt.upper() == "INTERVALLO":
                        continue
                    txt_up = txt.upper()
                    if txt_up == "DISPOSIZIONE":
                        sched[g][ORE[t]] = {"tipo": "disposizione"}
                        continue
                    if txt_up == "STAFF":
                        sched[g][ORE[t]] = {"tipo": "staff"}
                        continue
                    if txt_up == "UFFICIO TECNICO":
                        sched[g][ORE[t]] = {"tipo": "ufficio_tecnico"}
                        continue
                    if txt_up == "PUNTO VENDITA":
                        sched[g][ORE[t]] = {"tipo": "punto_vendita"}
                        continue
                    m = RE_CELLA_DOC.match(txt)
                    if not m:
                        report["docenti_celle_non_riconosciute"].append(f"{nome} {g} {t}: {txt}")
                        continue
                    sched[g][ORE[t]] = {"tipo": "lezione",
                                        "classe": norm_classe(m.group(1) + m.group(2)),
                                        "aula": norm_aula(m.group(3)) or None}
    return out, periodo


def parse_laboratori(report):
    """Estrae l'orario ufficiale dei laboratori da data/Orario-Laboratori.pdf se presente."""
    if not os.path.exists(PDF_LABORATORI):
        return {}
    labs = {}
    with pdfplumber.open(PDF_LABORATORI) as pdf:
        for page in pdf.pages:
            tables = page.dedupe_chars(tolerance=1).extract_tables()
            for tb in tables:
                if len(tb) < 2 or not tb[0][1]:
                    continue
                lab_name = norm_aula(tb[0][1])
                day_map = [("lunedi", 1, 2), ("martedi", 3, 4), ("mercoledi", 5, 6), ("giovedi", 7, 8), ("venerdi", 9, 10)]
                lab_sched = labs.setdefault(lab_name, {g: {} for g in GIORNI})

                for row in tb[2:]:
                    ora_str = cell(row[0])
                    if ora_str not in ORE:
                        continue
                    ora_num = ORE[ora_str]
                    for g, c_doc, c_cls in day_map:
                        doc = cell(row[c_doc]) if c_doc < len(row) else ""
                        cls = cell(row[c_cls]) if c_cls < len(row) else ""
                        if doc or cls:
                            slot = lab_sched[g].setdefault(ora_num, {"docenti": [], "classi": []})
                            if doc and doc not in slot["docenti"]:
                                slot["docenti"].append(doc)
                            if cls:
                                norm_c = norm_classe(cls)
                                if norm_c not in slot["classi"]:
                                    slot["classi"].append(norm_c)
    return labs


# ---------------------------------------------------------------- riconciliazione
RE_CODICE = re.compile(r"^[A-Z]\d{3}\b")


def compatto(n):
    return norm_persona(n).replace(" ", "")


def risolvi_docenti(classi_raw, docenti_raw, report):
    """
    1) Nomi troncati/spezzati in modo diverso nei due PDF ('BITTANTE ANNAMARI A' vs
       'BITTANTE ANNAMARIA'): match per prefisso sul nome compatto, solo se univoco.
    2) Tabelle intestate con un codice ('A026', 'B011'): il nome reale si ricava dalla
       classe che quello slot ha nel PDF classi (voto di maggioranza >= 80%).
    Ritorna (docenti_raw con chiavi canoniche, funzione nome_raw -> nome canonico).
    """
    reali = [n for n in docenti_raw if not RE_CODICE.match(n)]

    def canon(raw):
        k = compatto(raw)
        esatti = [n for n in reali if compatto(n) == k]
        if esatti:
            return esatti[0]
        cand = [n for n in reali if k.startswith(compatto(n)) or compatto(n).startswith(k)]
        if len(cand) == 1:
            return cand[0]
        if len(cand) > 1:
            report["nomi_ambigui"].append(f"{raw}: {cand}")
        return raw

    alias = {}
    for n, sched in docenti_raw.items():
        if not RE_CODICE.match(n):
            continue
        voti = {}
        # se il codice compare come docente nelle classi ("A026 1", "B011") e' un docente a se'
        if any(compatto(d) == compatto(n) for cs_g in classi_raw.values() for gg in cs_g.values()
               for sl in gg.values() for d in sl["docenti"]):
            continue
        for g, ore in sched.items():
            for ora, s in ore.items():
                cs = classi_raw.get(s.get("classe"), {}).get(g, {}).get(ora)
                if cs and cs["docenti"]:
                    c = canon(cs["docenti"][0])
                    voti[c] = voti.get(c, 0) + 1
        tot = sum(voti.values())
        best = max(voti, key=voti.get) if voti else None
        if best and voti[best] / tot >= 0.8:
            alias[n] = best
            report["alias_codici"].append(f"{n} -> {best} ({voti[best]}/{tot} slot)")
        else:
            report["codici_non_risolti"].append(f"{n}: {voti}")

    uniti = {}
    for n, sched in docenti_raw.items():
        nome = alias.get(n, n)
        dst = uniti.setdefault(nome, {g: {} for g in GIORNI})
        for g, ore in sched.items():
            for ora, s in ore.items():
                if ora in dst[g]:
                    report["slot_duplicati_docente"].append(f"{nome} {g} ora {ora} (da {n})")
                dst[g][ora] = s
    return uniti, canon


def riconcilia(classi_raw, docenti_raw, report, aule_classi=None, labs_raw=None):
    aule_classi = aule_classi or {}
    labs_raw = labs_raw or {}
    docenti_raw, canon = risolvi_docenti(classi_raw, docenti_raw, report)

    classi = {}
    for cid, sched in classi_raw.items():
        default_aula = aule_classi.get(cid)
        classi[cid] = {g: {} for g in GIORNI}
        for g, ore in sched.items():
            for ora, s in ore.items():
                classi[cid][g][ora] = {
                    "materia": s["materia"],
                    "docenti": [canon(d) for d in s["docenti"]],
                    "aula": s["aula"] or default_aula,
                }

    # 1) Arricchimento preliminare da Orario-Laboratori.pdf se presente
    for lab_name, sched in labs_raw.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                for c in s["classi"]:
                    if c in classi and ora in classi[c][g]:
                        classi[c][g][ora]["aula"] = lab_name
                        for d in s["docenti"]:
                            can_d = canon(d)
                            if can_d and can_d not in classi[c][g][ora]["docenti"]:
                                classi[c][g][ora]["docenti"].append(can_d)

    # 2) Arricchimento bidirezionale da docenti_raw
    for nome, sched in docenti_raw.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                if s["tipo"] == "lezione":
                    cid = s.get("classe")
                    if cid and cid in classi and ora in classi[cid][g]:
                        cs = classi[cid][g][ora]
                        if nome not in cs["docenti"]:
                            cs["docenti"].append(nome)
                        if s.get("aula"):
                            cs["aula"] = s["aula"]

    docenti = {n: {g: {} for g in GIORNI} for n in docenti_raw}

    # 3) Popola orario docenti con materia, aula e compresenze dalla classe corrispondente
    for nome, sched in docenti_raw.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                if s["tipo"] in ("disposizione", "staff", "ufficio_tecnico", "punto_vendita"):
                    docenti[nome][g][ora] = {"tipo": s["tipo"]}
                    continue
                default_aula = aule_classi.get(s["classe"])
                cl = classi.get(s["classe"])
                if cl is None:
                    report["docenti_classe_inesistente"].append(f"{nome} {g} ora {ora}: {s['classe']}")
                    docenti[nome][g][ora] = {"tipo": "lezione", "classe": s["classe"], "materia": None,
                                             "aula": s["aula"] or default_aula, "copresenza": [], "fonte": "docenti"}
                    continue
                cs = cl[g].get(ora)
                if cs is None:
                    report["docenti_slot_assente_in_classi"].append(f"{nome} {g} ora {ora}: {s['classe']}")
                    docenti[nome][g][ora] = {"tipo": "lezione", "classe": s["classe"], "materia": None,
                                             "aula": s["aula"] or default_aula, "copresenza": [], "fonte": "docenti"}
                    continue

                aula = s["aula"] or cs.get("aula") or default_aula
                if s["aula"] and cs.get("aula") and s["aula"] != cs["aula"] and cs["aula"] != default_aula:
                    report["conflitti_aula"].append(
                        f"{nome} {g} ora {ora} {s['classe']}: docenti={s['aula']} classi={cs['aula']}")

                altri = [d for d in cs["docenti"] if d != nome]
                docenti[nome][g][ora] = {
                    "tipo": "lezione" if (not cs["docenti"] or cs["docenti"][0] == nome) else "compresenza",
                    "classe": s["classe"],
                    "materia": cs["materia"],
                    "aula": aula,
                    "copresenza": altri,
                    "fonte": "docenti+classi",
                }

    # 4) Slot presenti nelle classi ma assenti dal PDF docenti
    for cid, sched in classi.items():
        default_aula = aule_classi.get(cid)
        for g, ore in sched.items():
            for ora, cs in ore.items():
                for d in cs["docenti"]:
                    if d not in docenti:
                        docenti[d] = {gg: {} for gg in GIORNI}
                        report["docenti_solo_in_classi"].append(d)
                    if ora not in docenti[d][g]:
                        report["slot_classi_mancanti_in_docenti"].append(f"{d} {g} ora {ora}: {cid}")
                        docenti[d][g][ora] = {
                            "tipo": "lezione" if cs["docenti"][0] == d else "compresenza",
                            "classe": cid, "materia": cs["materia"], "aula": cs["aula"] or default_aula,
                            "copresenza": [x for x in cs["docenti"] if x != d], "fonte": "classi"}
                    elif docenti[d][g][ora].get("classe") not in (None, cid):
                        slot = docenti[d][g][ora]
                        if cid not in slot.setdefault("altre_classi", []):
                            slot["altre_classi"].append(cid)
                            report["classi_articolate"].append(f"{d} {g} ora {ora}: {slot['classe']} + {cid}")
    return classi, docenti


def costruisci_aule(classi, docenti, labs_raw=None):
    """Occupazione per aula speciale (laboratori, palestra, ecc.)."""
    occ = defaultdict(lambda: {g: {} for g in GIORNI})
    labs_raw = labs_raw or {}

    # 1. Base ufficiale dai Laboratori (se presente il PDF)
    for lab_name, sched in labs_raw.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                cls_str = " + ".join(s["classi"])
                materia = ""
                for c in s["classi"]:
                    cl_slot = classi.get(c, {}).get(g, {}).get(ora)
                    if cl_slot and cl_slot.get("materia"):
                        materia = cl_slot["materia"]
                        break
                occ[lab_name][g][ora] = {
                    "classe": cls_str,
                    "materia": materia,
                    "docenti": list(s["docenti"])
                }

    # 2. Integrazione da docenti per palestre e spazi non nel PDF laboratori
    for nome, sched in docenti.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                aula = s.get("aula")
                if s.get("tipo") in ("disposizione", "staff", "ufficio_tecnico", "punto_vendita") or not aula or not RE_AULA_SPECIALE.match(aula):
                    continue
                e = occ[aula][g].setdefault(ora, {"classe": s.get("classe", ""), "materia": s.get("materia", ""), "docenti": []})
                if not e.get("materia") and s.get("materia"):
                    e["materia"] = s["materia"]
                if nome not in e["docenti"]:
                    e["docenti"].append(nome)

    # 3. Integrazione da classi per palestre e spazi speciali
    for cid, sched in classi.items():
        for g, ore in sched.items():
            for ora, s in ore.items():
                aula = s.get("aula")
                if aula and RE_AULA_SPECIALE.match(aula):
                    e = occ[aula][g].setdefault(ora, {"classe": cid, "materia": s.get("materia", ""), "docenti": []})
                    if not e.get("materia") and s.get("materia"):
                        e["materia"] = s["materia"]
                    for d in s.get("docenti", []):
                        if d not in e["docenti"]:
                            e["docenti"].append(d)

    return {a: occ[a] for a in sorted(occ)}


def aule_libere(aule):
    ore = sorted(ORE.values())
    return {g: {str(o): sorted(a for a in aule if o not in aule[a][g]) for o in ore} for g in GIORNI[:5]}


# ---------------------------------------------------------------- main
def run():
    keys = ["classi_tabelle_ignorate", "classi_righe_sconosciute", "classi_slot_senza_docente",
            "docenti_tabelle_ignorate", "docenti_celle_non_riconosciute", "docenti_classe_inesistente",
            "docenti_slot_assente_in_classi", "docenti_non_presente_nella_classe", "conflitti_aula",
            "classi_articolate", "docenti_solo_in_classi", "slot_classi_mancanti_in_docenti",
            "nomi_ambigui", "alias_codici", "codici_non_risolti", "slot_duplicati_docente"]
    report = {k: [] for k in keys}

    aule_classi = parse_griglia_classi()
    classi_raw = parse_classi(report)
    docenti_raw, periodo = parse_docenti(report)
    labs_raw = parse_laboratori(report)
    classi, docenti = riconcilia(classi_raw, docenti_raw, report, aule_classi, labs_raw)
    aule = costruisci_aule(classi, docenti, labs_raw)

    def ids(d):
        return {persona_id(n): n for n in d}
    dataset = {
        "meta": {"istituto": "IIS Alberto Parolini", "periodo": periodo, "ore": ORE_INFO,
                 "giorni": GIORNI[:5],
                 "nota_aule": "Le aule ordinarie non compaiono nei PDF: aule/aule_libere coprono solo laboratori e palestra."},
        "docenti": {persona_id(n): {"nome": n, "senza_nome": bool(re.match(r"^[A-Z]\d{3}", n)), "orario": s}
                    for n, s in sorted(docenti.items())},
        "classi": dict(sorted(classi.items())),
        "aule": aule,
        "aule_classi": aule_classi,
        "aule_libere": aule_libere(aule),
    }

    assert len(ids(docenti)) == len(docenti), "collisione id docenti"

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=1)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print(f"OK: {len(docenti)} docenti, {len(classi)} classi, {len(aule)} aule speciali -> {OUT_JSON}")
    for k, v in report.items():
        if v:
            print(f"  ⚠ {k}: {len(v)}")


if __name__ == "__main__":
    run()
