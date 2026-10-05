import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { buildIndex, parseQuery, answer } from "../public/js/query-engine.js";

const db = JSON.parse(readFileSync(new URL("../public/data/orario_completo.json", import.meta.url)));
const idx = buildIndex(db);
const now = new Date("2026-10-07T10:00:00"); // mercoledì
const ask = (t, me = "") => { const p = parseQuery(t, idx, { now, me }); return { p, a: answer(p, idx) }; };

let r = ask("Dove si trova la 4AS alla terza ora?");
assert.equal(r.p.intent, "classe"); assert.deepEqual(r.p.classi, ["4AS"]); assert.equal(r.p.hour, 3);
console.log(r.a.speech);

for (const v of ["4 as giovedì alla terza ora", "quarta AS giovedi 3 ora", "[4 AS] giovedì ora 3", "4as giovedì alla 3"]) {
  r = ask(v); assert.deepEqual(r.p.classi, ["4AS"], v); assert.equal(r.p.hour, 3, v); assert.equal(r.p.day, "giovedi", v);
}
r = ask("dove è la 4grf venerdì"); assert.deepEqual(r.p.classi.sort(), ["4GRFA", "4GRFB"]); console.log(r.a.speech);

r = ask("Quali aule sono libere mercoledì alla seconda ora?");
assert.equal(r.p.intent, "aule_libere"); assert.equal(r.p.hour, 2); console.log(r.a.speech);
r = ask("quali laboratori sono liberi lunedì"); assert.equal(r.p.hour, null); console.log(r.a.speech, r.a.lines.length);
r = ask("il laboratorio di chimica 1 è libero giovedì alla quarta ora?"); console.log(r.a.speech);
r = ask("chi c'è in palestra martedì alla seconda ora"); assert.equal(r.p.intent, "aula"); console.log(r.a.speech);

r = ask("Dove si trova Anzalone lunedì alla prima ora"); assert.equal(r.p.intent, "docente"); assert.match(r.a.speech, /in 1AP in aula T1/); console.log(r.a.speech);
r = ask("cosa ha anzolin domani"); console.log(r.a.speech);
r = ask("cosa ho domani", "Gemin"); assert.ok(r.p.isSelf); console.log(r.a.speech);
r = ask("dov'è romina"); assert.equal(r.p.intent, "ambiguo");
r = ask("quanto fa due più due"); assert.equal(r.p.intent, "non_trovato");
r = ask("cosa ha gemin sabato"); console.log(r.a.speech);
console.log("OK");
