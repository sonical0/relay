/**
 * relay-stop-hook.js — Vérifie que RELAY_LOG.md est à jour avant fin de session.
 * Installé comme hook Stop par relay-setup-hook.js.
 * Exit 2 + JSON {decision:"block", reason:"..."} → Claude reçoit le message et doit agir.
 */

const fs   = require('fs');
const path = require('path');

// RELAY_ROOT = dossier parent de .claude/hooks/
const relayRoot = path.join(__dirname, '..', '..');
const RELAY_LOG = path.join(relayRoot, 'RELAY_LOG.md');

// Silencieux si RELAY_LOG absent (pas encore créé sur ce poste)
if (!fs.existsSync(RELAY_LOG)) process.exit(0);

const today   = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
const content = fs.readFileSync(RELAY_LOG, 'utf8');

// Silencieux si le fichier ne contient pas encore d'entrées (stub vierge)
const hasEntries = /^## \d{4}-\d{2}-\d{2}/m.test(content);
if (!hasEntries) process.exit(0);

if (!content.includes(today)) {
  process.stdout.write(JSON.stringify({
    decision: 'block',
    reason:
      `[RELAY] Session du ${today} non loguée dans RELAY_LOG.md.\n` +
      `Mettre à jour ${RELAY_LOG} avec un résumé de cette session ` +
      `(format Relay : ## ${today} — Agent (projet : sujet) + max 5 bullets) avant de terminer.`,
  }));
  process.exit(2);
}

process.exit(0);
