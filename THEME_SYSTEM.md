# Système de thème — Documentation complète
> Réutilisable dans n'importe quel projet web (Django, React, HTML pur…).
> Fichiers sources : `static/css/style.css` · `static/js/theme.js`

---

## Architecture générale

Le système repose sur **trois mécanismes** qui s'enchaînent :

```
Utilisateur clique
      │
      ▼
window.setXxx()  ←── fonctions publiques dans theme.js
      │
      ├─► localStorage  (persistance entre sessions)
      │
      ├─► :root CSS vars  (--bg, --accent, --border, --font-ui…)
      │       └─► tout le CSS les lit via var(--xxx)
      │
      └─► data-theme / data-style sur <html>
              └─► les sélecteurs CSS [data-theme="dark"] etc. activent des blocs
```

**Règle clé :** Tout le CSS utilise uniquement des `var(--xxx)`. Jamais de couleurs ou tailles codées en dur dans les composants. Changer une var change instantanément toute l'interface.

---

## 1. Tokens CSS (variables de design)

Définis sur `:root` dans `style.css`. Ce sont les valeurs par défaut (mode clair).

```css
:root {
    /* Polices */
    --font-ui: system-ui, -apple-system, sans-serif;  /* Dashboard */
    --font-mc: 'Space Mono', 'Courier New', monospace; /* Mission Control */

    /* Fond de page et surfaces */
    --bg:       #f5f5f0;   /* fond page */
    --surface:  #ffffff;   /* fond cartes/panels */
    --surface2: #f0f0ea;   /* fond secondaire */

    /* Bordures */
    --border:   #d8dac8;   /* bordure principale */
    --border2:  #e8eacc;   /* bordure secondaire (plus subtile) */

    /* Texte */
    --tx-1: #131114;   /* texte principal */
    --tx-2: #3d3d38;   /* texte secondaire */
    --tx-3: #7a7a6e;   /* texte atténué / labels */

    /* Couleur accent (personnalisable) */
    --accent:      #8C9C7C;
    --accent-rgb:  140,156,124;   /* pour rgba() */
    --accent-lt:   #e8eacc;      /* version claire */
    --accent-text: #4a5a3c;      /* texte sur accent */

    /* Sémantique */
    --c-green: #5a7a4a;   --c-green-lt: #dde8d4;
    --c-red:   #8b3a3a;   --c-red-lt:   #f0dede;
    --c-amber: #7a6030;   --c-amber-lt: #ede8d4;
    --c-blue:  #3a5a7a;   --c-blue-lt:  #d4e0ed;

    /* Coins et ombres */
    --r:    4px;    /* border-radius standard */
    --r-lg: 6px;    /* border-radius large (cartes) */
    --sh:   0 1px 3px rgba(19,17,20,.07);
    --sh-md:0 4px 16px rgba(19,17,20,.10);

    /* Sidebar */
    --sidebar-bg: #131114;
    --sidebar-w:  240px;
    --header-h:   56px;
}
```

**Comment les utiliser dans les composants :**
```css
.ma-carte {
    background: var(--surface);        /* s'adapte auto au mode sombre */
    border: 1px solid var(--border);   /* s'adapte auto aux persos */
    border-radius: var(--r-lg);        /* s'adapte auto aux coins choisis */
    color: var(--tx-1);
    font-family: var(--font-ui);
    box-shadow: var(--sh);
}
```

---

## 2. Mode sombre / clair

### Comment ça marche

L'attribut `data-theme="dark"` sur `<html>` active un bloc CSS qui redéfinit les tokens :

```css
/* Mode clair : rien sur <html>, les :root s'appliquent */

/* Mode sombre : data-theme="dark" sur <html> */
[data-theme="dark"] {
    --bg:      #0e0d10;
    --surface: #18161b;
    --border:  #2d2b32;
    --tx-1:    #E8EACC;
    --tx-2:    #b0b29c;
    --sh-md:   0 4px 20px rgba(0,0,0,.6);
    /* Les vars couleur restent identiques — seules les surfaces changent */
}
```

### Activation JS
```js
// Passer en sombre
document.documentElement.setAttribute('data-theme', 'dark');

// Passer en clair
document.documentElement.removeAttribute('data-theme');
```

### Affichage dans l'UI
```html
<div class="mode-btns">
    <button onclick="setMode('light')" id="modeLight" class="active">☀ Clair</button>
    <button onclick="setMode('dark')"  id="modeDark">☾ Sombre</button>
</div>
```
`setMode()` gère aussi l'auto-ajustement de la couleur texte.

---

## 3. Styles visuels

### Comment ça marche

L'attribut `data-style="<nom>"` sur `<html>` active un style visuel.
Les composants utilisent les classes `.panel` et `.kpi-card` — les styles les ciblent.

```css
/* Flat (défaut) — rien à faire, styles de base s'appliquent */

/* Ombres */
[data-style="shadows"] .panel,
[data-style="shadows"] .kpi-card {
    box-shadow: var(--sh-md);
}

/* Soft — ombres sans bordure */
[data-style="soft"] .panel,
[data-style="soft"] .kpi-card {
    border: none;
    box-shadow: 0 4px 20px rgba(0,0,0,.10);
}

/* Bordered — contours épais */
[data-style="bordered"] .panel,
[data-style="bordered"] .kpi-card {
    border-width: 2px;
    box-shadow: none;
}

/* Minimal — fond alternatif, ni bordure ni ombre */
[data-style="minimal"] .panel,
[data-style="minimal"] .kpi-card {
    border: none;
    box-shadow: none;
    background: var(--surface2);
}

/* Glass — glassmorphism */
[data-style="glass"] .panel,
[data-style="glass"] .kpi-card {
    background: rgba(255,255,255,.55);
    backdrop-filter: blur(14px);
    border-color: rgba(255,255,255,.35);
}
[data-theme="dark"][data-style="glass"] .panel {
    background: rgba(24,22,27,.65);
    border-color: rgba(255,255,255,.08);
}

/* Cyber — dégradés teintés par l'accent */
[data-style="cyber"] {
    --border:  rgba(var(--accent-rgb), .28);
    --border2: rgba(var(--accent-rgb), .15);
}
[data-style="cyber"] .panel {
    border: 1px solid rgba(var(--accent-rgb),.2);
    background: linear-gradient(145deg,
        color-mix(in srgb, var(--surface) 96%, var(--accent) 4%) 0%,
        var(--surface) 100%);
}
[data-theme="dark"][data-style="cyber"] {
    --bg:      #07080b;
    --surface: #0d0f14;
}
```

### Activation JS
```js
document.documentElement.setAttribute('data-style', 'cyber');
```

### Boutons dans l'UI
```html
<div class="style-grid" id="styleGrid">
    <button class="style-btn active" data-vstyle="flat"     onclick="setVisualStyle('flat')">
        <div class="style-preview style-preview-flat"></div>
        <span>Flat</span>
    </button>
    <button class="style-btn" data-vstyle="shadows"  onclick="setVisualStyle('shadows')">
        <div class="style-preview style-preview-shadows"></div>
        <span>Ombres</span>
    </button>
    <button class="style-btn" data-vstyle="soft"     onclick="setVisualStyle('soft')">
        <div class="style-preview style-preview-soft"></div>
        <span>Soft</span>
    </button>
    <button class="style-btn" data-vstyle="bordered" onclick="setVisualStyle('bordered')">
        <div class="style-preview style-preview-bordered"></div>
        <span>Bordures</span>
    </button>
    <button class="style-btn" data-vstyle="minimal"  onclick="setVisualStyle('minimal')">
        <div class="style-preview style-preview-minimal"></div>
        <span>Minimal</span>
    </button>
    <button class="style-btn" data-vstyle="glass"    onclick="setVisualStyle('glass')">
        <div class="style-preview style-preview-glass"></div>
        <span>Glass</span>
    </button>
    <button class="style-btn" data-vstyle="cyber"    onclick="setVisualStyle('cyber')">
        <div class="style-preview style-preview-cyber"></div>
        <span>Cyber</span>
    </button>
</div>
```

CSS des mini-aperçus :
```css
.style-preview {
    width: 28px; height: 20px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 2px; margin: 0 auto .3rem;
}
.style-preview-flat    { border: 1px solid var(--border); box-shadow: none; }
.style-preview-shadows { border: 1px solid var(--border); box-shadow: 0 3px 8px rgba(0,0,0,.18); }
.style-preview-soft    { border: none; box-shadow: 0 4px 14px rgba(0,0,0,.14); }
.style-preview-bordered{ border: 2px solid var(--accent); box-shadow: none; }
.style-preview-minimal { border: none; background: var(--surface2); }
.style-preview-glass   { border: 1px solid rgba(255,255,255,.4); background: rgba(255,255,255,.4); backdrop-filter: blur(4px); }
.style-preview-cyber   { border: 1px solid rgba(140,156,124,.5); background: linear-gradient(135deg,#0d0f14,#07080b); box-shadow: 0 0 6px rgba(140,156,124,.25); }
```

Le bouton actif est marqué via JS : `el.classList.toggle('active', el.dataset.vstyle === currentStyle)`.

### Ajouter un nouveau style visuel
```css
/* 1. Dans style.css */
[data-style="mon-style"] .panel,
[data-style="mon-style"] .kpi-card {
    /* vos overrides */
}
[data-theme="dark"][data-style="mon-style"] .panel {
    /* variante dark si besoin */
}
```
```html
<!-- 2. Bouton dans le panel -->
<button class="style-btn" data-vstyle="mon-style" onclick="setVisualStyle('mon-style')">
    <div class="style-preview" style="/* mini aperçu inline */"></div>
    <span>Mon style</span>
</button>
```

---

## 4. Couleur accent (color picker)

### Comment ça marche

L'accent définit `--accent`, `--accent-rgb`, `--accent-lt`, `--accent-text` sur `:root`.
Tout bouton actif, highlight, nav active, badge utilise `var(--accent)`.

```css
.btn-primary    { background: var(--accent); color: var(--accent-text); }
.nav-link.active{ background: rgba(var(--accent-rgb), .15); border-left: 2px solid var(--accent); }
.badge-accent   { background: var(--accent-lt); color: var(--accent-text); }
```

### UI du picker
```html
<!-- Roue chromatique native -->
<div class="color-picker-wrap">
    <input type="color" id="accentColorPicker" value="#8C9C7C">
    <div class="color-picker-thumb" id="accentPickerThumb" style="background:#8C9C7C;"></div>
</div>
<!-- Hex input -->
<div class="color-hex-wrap">
    <span>#</span>
    <input type="text" id="accentHexInput" value="8C9C7C" maxlength="6" placeholder="8C9C7C">
    <button class="color-hex-apply" onclick="applyAccentHex()">↵</button>
</div>
<!-- Swatches prédéfinies -->
<div class="accent-swatches">
    <div class="accent-swatch" data-color="#8C9C7C" data-rgb="140,156,124" style="background:#8C9C7C;"></div>
    <div class="accent-swatch" data-color="#3b82f6" data-rgb="59,130,246"  style="background:#3b82f6;"></div>
    <!-- ... autres couleurs ... -->
</div>
```

Le JS écoute `input` sur la roue et `keydown Enter` sur le hex. La swatch active reçoit la classe `active`.

---

## 5. Couleurs personnalisables (fond, bordures)

### Comment ça marche

Ces couleurs sont appliquées **directement** en `style` inline sur `:root` via JS — elles prennent priorité sur le CSS statique. Pour réinitialiser, on appelle `removeProperty()` et le mode (clair/sombre) reprend le contrôle.

```js
// Appliquer
document.documentElement.style.setProperty('--bg', '#1a1a2e');
document.documentElement.style.setProperty('--border', '#2d2b32');

// Réinitialiser — laisse le mode (clair/sombre) reprendre
document.documentElement.style.removeProperty('--bg');
document.documentElement.style.removeProperty('--border');
```

### UI (même pattern pour fond et bordures)
```html
<div class="theme-section">
    <div class="theme-section-label">Fond principal</div>
    <div style="display:flex;align-items:center;gap:.5rem;">
        <!-- Roue chromatique -->
        <div class="color-picker-wrap">
            <input type="color" id="pageBgPicker" value="#f5f5f0" oninput="...">
            <div class="color-picker-thumb" id="pageBgPickerThumb"></div>
        </div>
        <!-- Hex -->
        <div class="color-hex-wrap">
            <span>#</span>
            <input type="text" id="pageBgHexInput" maxlength="6">
            <button onclick="applyPageBgHex()">↵</button>
        </div>
        <!-- Reset -->
        <button onclick="clearPageBg()" title="Réinitialiser">×</button>
    </div>
    <!-- Swatches -->
    <div class="accent-swatches">
        <div class="page-bg-swatch" data-color=""        style="background:var(--bg);border:1px dashed var(--border);" title="Auto"></div>
        <div class="page-bg-swatch" data-color="#0e0d10" style="background:#0e0d10;"></div>
        <!-- ... -->
    </div>
</div>
```

La swatch avec `data-color=""` déclenche `clearPageBg()` (reset).

---

## 6. Coins (border-radius)

### Comment ça marche

Tout le CSS utilise `var(--r)` et `var(--r-lg)`. Changer ces deux vars modifie tous les coins de l'interface.

```css
.panel       { border-radius: var(--r-lg); }
.kpi-card    { border-radius: var(--r-lg); }
.btn         { border-radius: var(--r); }
.badge       { border-radius: var(--r); }
```

### UI
```html
<div class="radius-row">
    <button class="radius-btn active" data-radius="0px,0px"   onclick="setRadius(this,'0px','0px')">
        <div class="radius-preview" style="border-radius:0"></div>
        Carré
    </button>
    <button class="radius-btn" data-radius="8px,10px"  onclick="setRadius(this,'8px','10px')">
        <div class="radius-preview" style="border-radius:4px"></div>
        Arrondi
    </button>
    <button class="radius-btn" data-radius="14px,18px" onclick="setRadius(this,'14px','18px')">
        <div class="radius-preview" style="border-radius:8px"></div>
        Large
    </button>
</div>
```

Le bouton actif est marqué via `data-radius` : le JS compare `r === p.radius`.

---

## 7. Polices

### Comment ça marche

Deux vars CSS contrôlent les polices :
- `--font-ui` : police du dashboard (body, tous les composants via `font-family: inherit`)
- `--font-mc` : police du Mission Control (section `#view-mc`)

```css
body      { font-family: var(--font-ui); }
#view-mc  { font-family: var(--font-mc); }
button, input, select { font-family: inherit; } /* héritent automatiquement */
```

### Chargement des polices (Google Fonts)
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700
    &family=Inter:wght@400;500;600;700
    &family=JetBrains+Mono:wght@400;700
    &family=Geist+Mono:wght@400;700
    &family=Sora:wght@400;500;600;700
    &family=DM+Sans:wght@400;500;600;700
    &family=IBM+Plex+Sans:wght@400;500;600;700
    &family=IBM+Plex+Mono:wght@400;700
    &display=swap" rel="stylesheet">
```

### Application JS
```js
// Police dashboard
const fontValue = font === 'system'
    ? 'system-ui, -apple-system, sans-serif'
    : `'${font}', sans-serif`;
document.documentElement.style.setProperty('--font-ui', fontValue);

// Police MC
const fontMcValue = font === 'system'
    ? 'monospace'
    : `'${font}', monospace`;
document.documentElement.style.setProperty('--font-mc', fontMcValue);
```

### UI (même pattern pour dashboard et MC)
```html
<div class="font-pick-grid" id="fontUiGrid">
    <button class="font-pick-btn active" data-font="system"
            onclick="setFontUi('system')"
            style="font-family:system-ui,sans-serif;">Système</button>
    <button class="font-pick-btn" data-font="Inter"
            onclick="setFontUi('Inter')"
            style="font-family:'Inter',sans-serif;">Inter</button>
    <!-- chaque bouton est affiché dans sa propre police via style inline -->
</div>
```

CSS de la grille :
```css
.font-pick-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: .3rem;
}
.font-pick-btn {
    padding: .35rem .5rem; font-size: .72rem; font-weight: 500;
    border: 1px solid var(--border); background: var(--surface2);
    color: var(--tx-2); cursor: pointer; text-align: left;
    border-radius: var(--r); transition: all .12s;
}
.font-pick-btn:hover  { border-color: var(--accent); color: var(--tx-1); }
.font-pick-btn.active { border-color: var(--accent); background: var(--accent-lt);
                        color: var(--accent-text); font-weight: 700; }
```

Le bouton actif est marqué via `data-font` : `el.classList.toggle('active', el.dataset.font === currentFont)`.

---

## 8. theme.js — Moteur complet

### Structure du fichier
```
(function() {              ← IIFE pour ne pas polluer le scope global
    const STORAGE_KEY      ← clé localStorage
    const DEFAULTS         ← valeurs par défaut de toutes les prefs
    function load()        ← lit localStorage + merge avec DEFAULTS
    function save(prefs)   ← écrit dans localStorage
    let prefs = load()     ← état courant

    function applyAll(p)   ← applique TOUTES les prefs sur le DOM
    // helpers couleur : lightenHex, darkenHex, hexToRgb
    // helpers UI : syncPickerUI, syncColorPickerEl, wireColorPicker

    // API publique — tout sur window.xxx pour les onclick HTML
    window.setMode()
    window.setAccent()
    window.setSidebarColor()
    window.setRadius()
    window.setFontSize()
    window.setVisualStyle()
    window.setFontUi()
    window.setFontMc()
    window.setTextColor()
    window.setConBg()
    window.setPageBg() / clearPageBg()
    window.setBorderColor() / clearBorderColor()
    window.openThemePanel() / closeThemePanel()
    window.resetTheme()
    window.applyAccentHex() / applyPageBgHex() / applyBorderColorHex() / applyConBgHex()
    window._themeApplyAll  ← expose applyAll pour usage externe

    function wireSwatches() ← branche tous les event listeners

    DOMContentLoaded → applyAll + wireSwatches + syncPickerUI
    applyAll(prefs)  ← exécuté immédiatement pour éviter le flash
})();
```

### Objet prefs (localStorage)
```js
{
    mode:        'light',          // 'light' | 'dark'
    accent:      '#8C9C7C',
    accentRgb:   '140,156,124',
    accentLt:    '#e8eacc',
    accentText:  '#4a5a3c',
    sidebar:     '#131114',
    radius:      '0px',           // --r
    radiusLg:    '0px',           // --r-lg
    fontSize:    '14',            // px
    vstyle:      'flat',          // style visuel
    fontUi:      'system',        // police dashboard
    fontMc:      'Space Mono',    // police MC
    textColor:   '#131114',
    conBg:       '#06080d',       // fond constellation MC
    pageBg:      '',              // '' = auto (suit le mode)
    borderColor: '',              // '' = auto (suit le mode)
}
```

### Ce que fait applyAll()
1. `data-theme` sur `<html>` → mode clair/sombre
2. `data-style` sur `<html>` → style visuel
3. `--accent`, `--accent-rgb`, `--accent-lt`, `--accent-text` sur `:root`
4. `--tx-1`, `--text-primary` sur `:root`
5. `--sidebar-bg` + style direct sur `#appSidebar`
6. `--con-bg` sur `:root`
7. `--bg` sur `:root` si pageBg défini, sinon `removeProperty`
8. `--border`, `--border2`, `--border-light` si borderColor défini
9. `--r`, `--r-lg` sur `:root`
10. `--font-ui`, `--font-mc` sur `:root`
11. `font-size` sur `:root`
12. Sync de tous les pickers (valeur + thumb)
13. Marquage des boutons actifs (swatches, style-btn, radius-btn, font-pick-btn)

---

## 9. Panel de personnalisation HTML

### Structure du panel
```html
<!-- Overlay cliquable pour fermer -->
<div class="theme-overlay" id="themeOverlay" onclick="closeThemePanel()"></div>

<!-- Panel latéral -->
<div class="theme-panel" id="themePanel">
    <div class="theme-panel-header">
        <span>Personnalisation</span>
        <button onclick="closeThemePanel()">×</button>
    </div>

    <!-- Section type -->
    <div class="theme-section">
        <div class="theme-section-label">Nom de la section</div>
        <!-- contrôles ici -->
    </div>

    <!-- ... autres sections ... -->

    <button class="theme-reset-btn" onclick="resetTheme()">↺ Réinitialiser</button>
</div>

<!-- Bouton déclencheur flottant -->
<button class="theme-trigger-btn" onclick="openThemePanel()">🎨</button>
```

### CSS du panel
```css
.theme-panel {
    position: fixed; top: 0; right: -340px; width: 320px; height: 100vh;
    background: var(--surface); border-left: 1px solid var(--border);
    z-index: 9000; overflow-y: auto; transition: right .25s ease;
    padding: 1rem;
}
.theme-panel.open { right: 0; }

.theme-overlay {
    display: none; position: fixed; inset: 0; z-index: 8999;
    background: rgba(0,0,0,.2);
}
.theme-overlay.open { display: block; }

.theme-trigger-btn {
    position: fixed; bottom: 1.5rem; right: 1.5rem;
    width: 44px; height: 44px; border-radius: 50%;
    background: var(--accent); color: var(--accent-text);
    border: none; cursor: pointer; z-index: 8998;
    box-shadow: 0 2px 12px rgba(0,0,0,.2);
}

.theme-section { margin-bottom: 1.25rem; }
.theme-section-label {
    font-size: .62rem; font-weight: 700; color: var(--tx-3);
    letter-spacing: .1em; text-transform: uppercase; margin-bottom: .5rem;
}
```

### Sections dans l'ordre
| # | Section | Contrôle |
|---|---|---|
| 1 | Mode d'affichage | Boutons Clair / Sombre |
| 2 | Fond principal | Color picker + hex + swatches + reset |
| 3 | Couleur des bordures | Color picker + hex + swatches + reset |
| 4 | Couleur accent | Color picker + hex + 12 swatches |
| 5 | Couleur sidebar | 6 swatches |
| 6 | Fond Constellation (MC) | Color picker + hex + 8 swatches |
| 7 | Style visuel | 7 boutons avec aperçu |
| 8 | Coins | 3 boutons (Carré / Arrondi / Large) |
| 9 | Police — Dashboard | Grille 2 colonnes, 8 polices |
| 10 | Police — Mission Control | Grille 2 colonnes, 7 polices |
| 11 | Texte principal | 5 swatches |
| 12 | Taille du texte | Slider 12–16px |
| — | Réinitialiser | Bouton reset global |

---

## 10. Priorité des surcharges CSS

Du plus fort (inline JS) au plus faible (défaut `:root`) :

```
element.style.setProperty()              ← inline JS (pageBg, borderColor, fontUi, fontMc…)
  > #view-mc[data-mc-theme="dark/light"] ← mode MC forcé
    > #view-mc[data-mc-style="green/…"]  ← thème couleur MC
      > [data-theme="dark"][data-style="cyber"] .panel  ← combo thème+style
        > [data-style="cyber"] .panel    ← style visuel seul
          > [data-theme="dark"]          ← mode sombre
            > :root                      ← défaut clair
```

---

## 11. Mission Control — Système de thème propre

Le MC a un jeu de vars CSS **indépendant** défini sur `#view-mc`, avec son propre mode sombre/clair.

```css
/* Vars de base MC (sombre par défaut) */
#view-mc {
    --mc-bg:           #0b0c0e;
    --mc-surface:      #0e1117;
    --mc-border-theme: #1e2a3a;
    --mc-con-bg:       #06080d;
    --mc-hi:           #f59e0b;   /* couleur principale */
    --mc-tx:           #cbd5e1;
    --mc-border: var(--mc-border-custom, var(--mc-border-theme));
    /* --mc-border-custom (picker MC) écrase --mc-border-theme (thème) */
}

/* Mode clair MC auto (suit le thème global) */
:root:not([data-theme="dark"]) #view-mc:not([data-mc-theme="dark"]) {
    --mc-bg:      #f4f5f0;
    --mc-surface: #ffffff;
    --mc-con-bg:  #e8eadc;
    --mc-tx:      #1e2118;
}

/* Mode sombre MC forcé (indépendant du thème global) */
#view-mc[data-mc-theme="dark"] { /* vars sombres */ }

/* Mode clair MC forcé */
#view-mc[data-mc-theme="light"] { /* vars claires */ }

/* Thèmes couleur (toujours sombres) */
#view-mc[data-mc-style="green"]  { --mc-hi:#22c55e; --mc-con-bg:#030a05; … }
#view-mc[data-mc-style="blue"]   { --mc-hi:#38bdf8; --mc-con-bg:#04060f; … }
#view-mc[data-mc-style="red"]    { --mc-hi:#ef4444; --mc-con-bg:#0a0303; … }
#view-mc[data-mc-style="amber"]  { --mc-hi:#f59e0b; --mc-con-bg:#090700; … }
```

Boutons dans la topbar MC :
```html
<div class="mc-style-bar">
    <button data-mcs="dark"  onclick="setMcStyle('dark')">DARK</button>
    <button data-mcs="light" onclick="setMcStyle('light')">LIGHT</button>
    <button data-mcs="green" onclick="setMcStyle('green')">GREEN</button>
    <!-- … -->
    <!-- Mini pickers directs dans la topbar -->
    <div class="mc-mini-picker">
        <input type="color" id="mcConBgPicker" oninput="mcSetConBg(this.value)">
        <div class="mc-mini-picker-thumb" id="mcConBgThumb"></div>
    </div>
</div>
```

---

## 12. Intégration dans un nouveau projet

### Fichiers à copier
```
static/css/style.css    → tokens + styles visuels + composants
static/js/theme.js      → moteur complet
```

### HTML minimum
```html
<!DOCTYPE html>
<html lang="fr">  <!-- pas de data-theme = mode clair par défaut -->
<head>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700
        &family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;700
        &family=Sora:wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700
        &family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;700
        &display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
</head>
<body>

    <!-- Ton contenu avec .panel, .kpi-card etc. -->
    <div class="panel"> ... </div>

    <!-- Panel de personnalisation (copier depuis base.html) -->
    <div class="theme-overlay" id="themeOverlay" onclick="closeThemePanel()"></div>
    <div class="theme-panel" id="themePanel"> ... </div>
    <button class="theme-trigger-btn" onclick="openThemePanel()">🎨</button>

    <!-- Moteur de thème — doit être chargé AVANT les scripts de page -->
    <script src="theme.js"></script>
</body>
</html>
```

### Règles à respecter pour que tout fonctionne

1. **Jamais de couleur codée en dur dans les composants** — toujours `var(--xxx)`
2. **Toujours `font-family: inherit`** sur `button`, `input`, `select` pour hériter `--font-ui`
3. **Utiliser `.panel` et `.kpi-card`** comme classes de base pour que les styles visuels s'appliquent
4. **`theme.js` chargé en dernier** (avant `</body>`) — il s'applique immédiatement pour éviter le flash
5. **Toutes les fonctions onclick doivent être sur `window.xxx`** (pas juste `function xxx()`) car le SPA les wrappe dans un IIFE

### Ajouter un composant qui répond aux thèmes
```css
.mon-composant {
    background: var(--surface);      /* auto clair/sombre */
    border: 1px solid var(--border); /* auto + perso bordures */
    border-radius: var(--r-lg);      /* auto coins */
    color: var(--tx-1);              /* auto clair/sombre */
    font-family: var(--font-ui);     /* auto police */
    box-shadow: var(--sh);           /* auto ombres */
}
/* Rien d'autre à faire — les data-theme et data-style gèrent le reste */
```
