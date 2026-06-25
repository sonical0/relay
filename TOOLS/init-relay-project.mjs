#!/usr/bin/env node
/**
 * init-relay-project.mjs — Initialise un nouveau projet dans l'architecture Relay.
 *
 * Usage: node TOOLS/init-relay-project.mjs <project-name>
 *
 * Crée :
 *   PROJECTS/<name>/.relay-bootstrap.md  (overrides locaux)
 *   PROJECTS/<name>/RELAY_LOG.md          (stub de redirection vers global)
 * Met à jour :
 *   relay-brain.yml → section projects:
 */

import fs   from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname  = path.dirname(fileURLToPath(import.meta.url));
const relayRoot  = path.join(__dirname, '..');
const brainPath  = path.join(relayRoot, 'relay-brain.yml');

async function initProject(name) {
  if (!name) {
    console.error('Usage: node TOOLS/init-relay-project.mjs <project-name>');
    process.exit(1);
  }

  const projectDir  = path.join(relayRoot, 'PROJECTS', name);
  const templateDir = path.join(relayRoot, 'PROJECTS', '_template');

  console.log(`Initialisation du projet : ${name}`);

  if (!fs.existsSync(projectDir)) {
    fs.mkdirSync(projectDir, { recursive: true });
    console.log(`Créé : ${projectDir}`);
  }

  // Copier les fichiers du template (en remplaçant {PROJECT_NAME})
  if (fs.existsSync(templateDir)) {
    for (const file of fs.readdirSync(templateDir)) {
      const src  = path.join(templateDir, file);
      const dest = path.join(projectDir, file);
      if (fs.existsSync(dest)) { console.log(`Existant (ignoré) : ${dest}`); continue; }
      let content = fs.readFileSync(src, 'utf8');
      content = content.replace(/\{PROJECT_NAME\}/g, name);
      fs.writeFileSync(dest, content, 'utf8');
      console.log(`Copié  : ${dest}`);
    }
  } else {
    // Fallback si template absent : créer les fichiers minimaux
    const bootstrapPath = path.join(projectDir, '.relay-bootstrap.md');
    if (!fs.existsSync(bootstrapPath)) {
      fs.writeFileSync(bootstrapPath, `# .relay-bootstrap.md — ${name} (overrides projet)
# Générique : ../../relay-bootstrap.md — lire D'ABORD, puis ce fichier.

Brain global  : ../../relay-brain.yml
RELAY_LOG     : ../../RELAY_LOG.md  (global — ne pas écrire dans le RELAY_LOG local)

## Overrides spécifiques à ${name}

<!-- Ajouter ici uniquement ce qui diffère des conventions globales -->
`, 'utf8');
      console.log(`Créé : ${bootstrapPath}`);
    }

    const logPath = path.join(projectDir, 'RELAY_LOG.md');
    if (!fs.existsSync(logPath)) {
      fs.writeFileSync(logPath, `# RELAY_LOG — ${name}\n\n> **Log global** : \`../../RELAY_LOG.md\`\n> Ne pas écrire ici — utiliser le fichier global pour la coordination inter-agents.\n`, 'utf8');
      console.log(`Créé : ${logPath}`);
    }
  }

  // Mise à jour relay-brain.yml
  if (fs.existsSync(brainPath)) {
    let brain = fs.readFileSync(brainPath, 'utf8');
    if (brain.includes(`\n  ${name}:\n`)) {
      console.log(`Projet "${name}" déjà présent dans relay-brain.yml`);
    } else {
      const entry = [
        `  ${name}:`,
        `    path: "PROJECTS/${name}"`,
        `    bootstrap: "PROJECTS/${name}/.relay-bootstrap.md"`,
        `    relay_log: "RELAY_LOG.md"`,
        `    claims: []`,
        `    known_issues: []`,
      ].join('\n');
      brain = brain.replace(/^(projects:)$/m, `$1\n${entry}`);
      fs.writeFileSync(brainPath, brain, 'utf8');
      console.log(`relay-brain.yml mis à jour avec : ${name}`);
    }
  } else {
    console.warn(`relay-brain.yml introuvable — mise à jour ignorée.`);
  }

  console.log(`\nProjet "${name}" initialisé.`);
  console.log(`Prochaine étape : compléter PROJECTS/${name}/.relay-bootstrap.md avec les règles spécifiques.`);
}

initProject(process.argv[2]);
