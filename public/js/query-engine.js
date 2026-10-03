// Motore di interrogazione dell'orario: nessuna dipendenza dal DOM.
// parseQuery(text, idx, ctx) -> intento strutturato
// answer(parsed, idx, ctx)   -> { title, badge, lines[], note, speech }

const JS_DAYS = ["domenica", "lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato"];
const ORD_F = { prima: 1, seconda: 2, terza: 3, quarta: 4, quinta: 5, sesta: 6, settima: 7, ottava: 8 };
const ORD_WORDS = Object.fromEntries(Object.entries(ORD_F).map(([w, n]) => [n, w]));
const LESSON_MIN = 55;

export function normalize(str) {
  return (str || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function titleCase(s) {
  return (s || "").toLowerCase().replace(/(^|[\s'])([a-zà-ÿ])/g, (m, p, c) => p + c.toUpperCase());
}

const DAY_LABEL = { lunedi: "lunedì", martedi: "martedì", mercoledi: "mercoledì", giovedi: "giovedì", venerdi: "venerdì", sabato: "sabato", domenica: "domenica" };
const dl = (d) => DAY_LABEL[d] || d;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// ------------------------------------------------------------------ indice
export function buildIndex(db) {
  const docenti = Object.entries(db.docenti)
    .filter(([, d]) => !d.senza_nome)
    .map(([id, d]) => ({ id, nome: d.nome, tokens: normalize(d.nome).split(" ").filter((t) => t.length >= 3) }));
  const classKeys = Object.keys(db.classi).map((id) => ({ id, key: id.toLowerCase() }));
  const aule = Object.keys(db.aule).map((nome) => ({
    nome,
    key: normalize(nome).replace(/^lab\s+/, ""),
  }));
  const starts = db.meta.ore.map((o) => {
    const [h, m] = o.inizio.split(":").map(Number);
    return { ora: o.ora, inizio: o.inizio, min: h * 60 + m };
  });
  return { db, docenti, classKeys, aule, starts };
}

// ------------------------------------------------------------------ entità
function findClasses(q, idx) {
  const exists = (cand) => idx.classKeys.some((c) => c.key.startsWith(cand));
  let s = q;
  // "quarta ita" -> "4ita"   |   "4 ita" -> "4ita"   (solo se esiste una classe con quel prefisso)
  s = s.replace(/\b(prima|seconda|terza|quarta|quinta)\s+([a-z]{2,5})\b/g, (m, o, l) =>
    exists(ORD_F[o] + l) ? ORD_F[o] + l : m);
  s = s.replace(/\b([1-5])\s+([a-z]{2,5})\b/g, (m, d, l) => (exists(d + l) ? d + l : m));

  const ids = [];
  const used = [];
  for (const tok of s.match(/\b[1-5][a-z]{2,5}\b/g) || []) {
    const exact = idx.classKeys.filter((c) => c.key === tok);
    const hits = exact.length ? exact : idx.classKeys.filter((c) => c.key.startsWith(tok));
    if (hits.length) {
      used.push(tok);
      hits.forEach((h) => !ids.includes(h.id) && ids.push(h.id));
    }
  }
  let rest = s;
  used.forEach((t) => (rest = rest.replace(t, " ")));
  return { ids, rest: rest.replace(/\s+/g, " ").trim() };
}

function scoreDocenti(q, idx) {
  const words = new Set(q.split(" ").filter((w) => w.length >= 3));
  const scored = idx.docenti
    .map((d) => ({ d, score: d.tokens.filter((t) => words.has(t)).length }))
    .filter((x) => x.score > 0);
  if (!scored.length) return [];
  const top = Math.max(...scored.map((x) => x.score));
  return scored.filter((x) => x.score === top).map((x) => x.d);
}

function findRooms(q, idx) {
  const exact = idx.aule.filter((a) => new RegExp(`(^|\\s)${a.key}(\\s|$)`).test(q));
  if (exact.length) return exact;
  const words = new Set(q.split(" "));
  return idx.aule.filter((a) => words.has(a.key.split(" ")[0]));
}

function findDay(q, now) {
  for (const d of ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]) {
    if (new RegExp(`\\b${d}\\b`).test(q)) return d;
  }
  const n = now.getDay();
  if (/\bdopodomani\b/.test(q)) return JS_DAYS[(n + 2) % 7];
  if (/\bdomani\b/.test(q)) return JS_DAYS[(n + 1) % 7];
  if (/\bieri\b/.test(q)) return JS_DAYS[(n + 6) % 7];
  return JS_DAYS[n];
}

function findHour(q) {
  const ord = Object.keys(ORD_F).join("|");
  let m =
    q.match(new RegExp(`\\b(${ord})\\s+(?:ora|lezione)\\b`)) ||
    q.match(new RegExp(`\\b(?:alla|nella|all|nell)\\s+(${ord})\\b`));
  if (m) return ORD_F[m[1]];
  m = q.match(/\b(?:ora|ore)\s+([1-8])\b/) || q.match(/\b([1-8])\s+ora\b/) || q.match(/\balla\s+([1-8])\b/);
  return m ? Number(m[1]) : null;
}

function currentLesson(now, idx) {
  const t = now.getHours() * 60 + now.getMinutes();
  const hit = idx.starts.find((s) => t >= s.min && t < s.min + LESSON_MIN);
  return hit ? hit.ora : null;
}

// ------------------------------------------------------------------ parsing
export function parseQuery(text, idx, ctx) {
  const now = ctx.now || new Date();
  const q0 = normalize(text);
  const { ids: classi, rest } = findClasses(q0, idx);
  const q = rest;

  const day = findDay(q, now);
  const realtime = /\b(adesso|attualmente|in questo momento|in questo istante|ora attuale)\b/.test(q);
  let hour = findHour(q);
  if (realtime) hour = currentLesson(now, idx);

  const base = { day, hour, realtime, raw: text };
  const mentionsRoomWord = /\b(aula|aule|laboratorio|laboratori|lab|palestra|stanza|stanze)\b/.test(q);
  const wantsFree = /\b(liber[aeio]|vuot[aeio]|disponibil[ei])\b/.test(q);
  const rooms = findRooms(q, idx);

  if (wantsFree && (mentionsRoomWord || rooms.length)) {
    return { ...base, intent: "aule_libere", rooms };
  }

  const docs = scoreDocenti(q, idx);
  if (docs.length > 1) return { ...base, intent: "ambiguo", candidati: docs };
  if (docs.length === 1) {
    const me = ctx.me ? scoreDocenti(normalize(ctx.me), idx) : [];
    return { ...base, intent: "docente", docente: docs[0], isSelf: me.length === 1 && me[0].id === docs[0].id };
  }
  if (classi.length) return { ...base, intent: "classe", classi };
  if (rooms.length) return { ...base, intent: "aula", rooms };

  const me = ctx.me ? scoreDocenti(normalize(ctx.me), idx) : [];
  if (me.length === 1) return { ...base, intent: "docente", docente: me[0], isSelf: true };
  return { ...base, intent: "non_trovato" };
}

// ------------------------------------------------------------------ risposte
function groupHours(daySched, sameKey) {
  const groups = [];
  let cur = null;
  for (let h = 1; h <= 8; h++) {
    const s = daySched[String(h)];
    if (!s) { if (cur) groups.push(cur); cur = null; continue; }
    if (cur && cur.end === h - 1 && sameKey(cur.slot) === sameKey(s)) cur.end = h;
    else { if (cur) groups.push(cur); cur = { start: h, end: h, slot: s }; }
  }
  if (cur) groups.push(cur);
  return groups;
}

const span = (g) => (g.start === g.end ? `${g.start}ª ora` : `${g.start}ª-${g.end}ª ora`);
const spokenSpan = (g) =>
  g.start === g.end ? `in ${ORD_WORDS[g.start]} ora`
    : g.end - g.start === 1 ? `in ${ORD_WORDS[g.start]} e ${ORD_WORDS[g.end]} ora`
      : `dalla ${ORD_WORDS[g.start]} alla ${ORD_WORDS[g.end]} ora`;
const joinSpeech = (parts) =>
  parts.length === 1 ? parts[0] : parts.slice(0, -1).join(", ") + ", e " + parts[parts.length - 1];
const startOf = (idx, h) => idx.starts.find((s) => s.ora === h)?.inizio;

function closedDay(p, title) {
  const d = dl(p.day === "domenica" ? "domenica" : "sabato");
  return { title, badge: d.toUpperCase(), lines: [], note: `Di ${d} non ci sono lezioni in questo orario.`,
    speech: `Di ${d} non ci sono lezioni.` };
}

function hourBadge(p, idx) {
  return `${dl(p.day).toUpperCase()} • ${p.hour}ª ORA (${startOf(idx, p.hour)})`;
}

function noHourNow(p) {
  return { title: "Nessuna lezione in corso", badge: "ADESSO", lines: [],
    note: "In questo momento non c'è lezione (intervallo o fuori orario).",
    speech: "In questo momento non c'è lezione: è intervallo o fuori orario." };
}

function answerDocente(p, idx) {
  const d = idx.db.docenti[p.docente.id];
  const nome = titleCase(d.nome);
  const title = p.isSelf ? `Prof. ${nome} (Tu)` : `Prof. ${nome}`;
  if (p.day === "sabato" || p.day === "domenica") return closedDay(p, title);
  if (p.realtime && p.hour === null) return { ...noHourNow(p), title };
  const sched = d.orario[p.day] || {};
  const dayCap = cap(dl(p.day));
  const subj = p.isSelf ? "" : `il professor ${nome}`;

  const describe = (s) => {
    if (s.tipo === "disposizione") return { main: "Disposizione", sub: "", say: p.isSelf ? "sarai a disposizione" : "è a disposizione" };
    const extra = [...(s.altre_classi || [])];
    const cls = [s.classe, ...extra].join(" + ");
    const con = s.copresenza?.length ? s.copresenza.map(titleCase).join(", ") : "";
    const mat = s.materia ? titleCase(s.materia) : "materia non indicata";
    const aula = s.aula ? titleCase(s.aula) : "aula ordinaria";
    const verb = s.tipo === "compresenza" ? "è in compresenza" : "ha lezione";
    return {
      main: `${aula} • ${cls}`,
      sub: `${mat}${con ? " · con " + con : ""}${s.tipo === "compresenza" ? " (compresenza)" : ""}`,
      say: `${p.isSelf ? (s.tipo === "compresenza" ? "sarai in compresenza" : "avrai lezione") : verb} di ${mat} con la ${cls}, in ${aula}${con ? ", insieme a " + con : ""}`,
    };
  };

  if (p.hour === null) {
    const groups = groupHours(sched, (s) => JSON.stringify([s.tipo, s.classe, s.materia, s.aula, s.copresenza, s.altre_classi]));
    if (!groups.length) {
      return { title, badge: `${dl(p.day).toUpperCase()} • GIORNATA`, lines: [], note: "Nessuna lezione in orario.",
        speech: p.isSelf ? `${dayCap} non hai lezioni in orario.` : `${dayCap} ${subj} non ha lezioni in orario.` };
    }
    const descs = groups.map((g) => ({ g, ...describe(g.slot) }));
    return {
      title, badge: `${dl(p.day).toUpperCase()} • GIORNATA INTERA`,
      lines: descs.map((x) => ({ when: span(x.g), main: x.main, sub: x.sub })),
      speech: `${dayCap} ${subj} ${joinSpeech(descs.map((x) => `${spokenSpan(x.g)} ${x.say}`))}.`.replace(/\s+/g, " "),
    };
  }

  const s = sched[String(p.hour)];
  const when = `${ORD_WORDS[p.hour]} ora`;
  if (!s) {
    return { title, badge: hourBadge(p, idx), lines: [], note: "Nessuna lezione in quest'ora.",
      speech: p.isSelf ? `${dayCap} in ${when} non hai lezioni.` : `${dayCap} in ${when} ${subj} non ha lezioni.` };
  }
  const x = describe(s);
  return { title, badge: hourBadge(p, idx), lines: [{ when: `${p.hour}ª ora`, main: x.main, sub: x.sub }],
    speech: `${dayCap} in ${when} ${subj} ${x.say}.`.replace(/\s+/g, " ") };
}

function answerClasse(p, idx) {
  const out = [];
  for (const cid of p.classi) {
    const sched = idx.db.classi[cid][p.day] || {};
    const describe = (s) => {
      const doc = s.docenti.map(titleCase).join(" e ");
      const mat = titleCase(s.materia);
      const where = s.aula ? `in ${titleCase(s.aula)}` : "nella sua aula (non indicata nell'orario)";
      return { main: `${mat}${doc ? " • " + doc : ""}`, sub: s.aula ? titleCase(s.aula) : "Aula ordinaria",
        say: `ha ${mat}${doc ? " con " + doc : ""}, ${where}`, mat, doc, where };
    };
    if (p.day === "sabato" || p.day === "domenica") { out.push(closedDay(p, `Classe ${cid}`)); continue; }
    if (p.realtime && p.hour === null) { out.push({ ...noHourNow(p), title: `Classe ${cid}` }); continue; }
    const dayCap = cap(dl(p.day));

    if (p.hour === null) {
      const groups = groupHours(sched, (s) => JSON.stringify([s.materia, s.docenti, s.aula]));
      if (!groups.length) {
        out.push({ title: `Classe ${cid}`, badge: `${dl(p.day).toUpperCase()} • GIORNATA`, lines: [], note: "Nessuna lezione in orario.",
          speech: `${dayCap} la ${cid} non ha lezioni in orario.` });
        continue;
      }
      const descs = groups.map((g) => ({ g, ...describe(g.slot) }));
      out.push({
        title: `Classe ${cid}`, badge: `${dl(p.day).toUpperCase()} • GIORNATA INTERA`,
        lines: descs.map((x) => ({ when: span(x.g), main: x.main, sub: x.sub })),
        speech: `${dayCap} la ${cid} ${joinSpeech(descs.map((x) => `${spokenSpan(x.g)} ${x.say}`))}.`,
      });
      continue;
    }
    const s = sched[String(p.hour)];
    const when = `${ORD_WORDS[p.hour]} ora`;
    if (!s) {
      out.push({ title: `Classe ${cid}`, badge: hourBadge(p, idx), lines: [], note: "Nessuna lezione in quest'ora.",
        speech: `${dayCap} in ${when} la ${cid} non ha lezione.` });
      continue;
    }
    const x = describe(s);
    out.push({ title: `Classe ${cid}`, badge: hourBadge(p, idx), lines: [{ when: `${p.hour}ª ora`, main: x.main, sub: x.sub }],
      speech: `${dayCap} in ${when} la ${cid} ${x.say}.` });
  }
  return mergeAnswers(out);
}

function mergeAnswers(list) {
  if (list.length === 1) return list[0];
  return {
    title: list.map((a) => a.title).join(" / "),
    badge: list[0].badge,
    lines: list.flatMap((a) => (a.lines.length ? [{ when: a.title, main: "", sub: "", heading: true }, ...a.lines]
      : [{ when: a.title, main: a.note || "", sub: "" }])),
    speech: list.map((a) => a.speech).join(" "),
  };
}

const NOTE_AULE = "Le aule ordinarie non compaiono nei PDF dell'orario: sono considerati solo laboratori e palestra.";

function answerAuleLibere(p, idx) {
  const title = "Aule libere";
  if (p.day === "sabato" || p.day === "domenica") return closedDay(p, title);
  if (p.realtime && p.hour === null) return { ...noHourNow(p), title };
  const libere = idx.db.aule_libere[p.day];
  const filter = (list) => (p.rooms.length ? list.filter((a) => p.rooms.some((r) => r.nome === a)) : list);
  const dayCap = cap(dl(p.day));

  if (p.hour === null) {
    const lines = Object.keys(libere).map((h) => ({
      when: `${h}ª ora`, main: filter(libere[h]).map(titleCase).join(", ") || "nessuna", sub: "" }));
    return { title, badge: `${dl(p.day).toUpperCase()} • TUTTE LE ORE`, lines, note: NOTE_AULE,
      speech: `Per quale ora? Ti mostro le aule libere di ${dl(p.day)} per ogni ora.` };
  }
  const free = filter(libere[String(p.hour)] || []);
  const when = `${ORD_WORDS[p.hour]} ora`;
  if (p.rooms.length) {
    const lines = p.rooms.map((r) => ({
      when: titleCase(r.nome), main: free.includes(r.nome) ? "Libera" : "Occupata", sub: "" }));
    return { title: "Disponibilità aula", badge: hourBadge(p, idx), lines, note: NOTE_AULE,
      speech: `${dayCap} in ${when}: ` + p.rooms.map((r) =>
        `${titleCase(r.nome)} è ${free.includes(r.nome) ? "libera" : "occupata"}`).join(", ") + "." };
  }
  return {
    title, badge: hourBadge(p, idx), lines: free.map((a) => ({ when: "Libera", main: titleCase(a), sub: "" })),
    note: free.length ? NOTE_AULE : `Tutti i laboratori e la palestra sono occupati. ${NOTE_AULE}`,
    speech: free.length
      ? `${dayCap} in ${when} sono liberi: ${joinSpeech(free.map(titleCase))}. Considero solo laboratori e palestra.`
      : `${dayCap} in ${when} tutti i laboratori e la palestra sono occupati.`,
  };
}

function answerAula(p, idx) {
  const out = [];
  for (const r of p.rooms) {
    const title = titleCase(r.nome);
    if (p.day === "sabato" || p.day === "domenica") { out.push(closedDay(p, title)); continue; }
    if (p.realtime && p.hour === null) { out.push({ ...noHourNow(p), title }); continue; }
    const occ = idx.db.aule[r.nome][p.day] || {};
    const dayCap = cap(dl(p.day));
    const desc = (o) => `${o.classe}${o.materia ? " (" + titleCase(o.materia) + ")" : ""}${o.docenti.length ? " con " + o.docenti.map(titleCase).join(" e ") : ""}`;
    if (p.hour === null) {
      const lines = Object.keys(occ).sort((a, b) => a - b).map((h) => ({ when: `${h}ª ora`, main: desc(occ[h]), sub: "" }));
      out.push({ title, badge: `${dl(p.day).toUpperCase()} • GIORNATA`, lines,
        note: lines.length ? "" : "Mai occupata in questa giornata.",
        speech: lines.length ? `${dayCap} ${title} è occupata in ${lines.length} ore.` : `${dayCap} ${title} è libera tutto il giorno.` });
      continue;
    }
    const o = occ[String(p.hour)];
    const when = `${ORD_WORDS[p.hour]} ora`;
    out.push(o
      ? { title, badge: hourBadge(p, idx), lines: [{ when: "Occupata", main: desc(o), sub: "" }],
          speech: `${dayCap} in ${when} ${title} è occupata dalla ${desc(o)}.` }
      : { title, badge: hourBadge(p, idx), lines: [{ when: "Libera", main: "", sub: "" }],
          speech: `${dayCap} in ${when} ${title} è libera.` });
  }
  return mergeAnswers(out);
}

export function answer(p, idx) {
  switch (p.intent) {
    case "docente": return answerDocente(p, idx);
    case "classe": return answerClasse(p, idx);
    case "aule_libere": return answerAuleLibere(p, idx);
    case "aula": return answerAula(p, idx);
    case "ambiguo": {
      const names = p.candidati.slice(0, 4).map((d) => titleCase(d.nome));
      return { title: "Quale docente?", badge: "AMBIGUO", lines: names.map((n) => ({ when: "", main: n, sub: "" })),
        note: "Ripeti la richiesta con nome e cognome.",
        speech: `Ho trovato più docenti: ${joinSpeech(names)}. Specifica nome e cognome.` };
    }
    default:
      return { title: "Non ho capito", badge: "", lines: [],
        note: "Prova: «Dove si trova la 4AS alla terza ora?», «Cosa ha Gemin domani?», «Quali aule sono libere mercoledì alla seconda ora?»",
        speech: "Non ho capito. Puoi chiedere di un docente, di una classe, oppure delle aule libere." };
  }
}
