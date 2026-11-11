# CAHIER DES CHARGES FONCTIONNEL
## Module de contrôle de conformité des articles – Product / Asten / Assortiment

**Version :** 1.0  
**Date :** 10 novembre 2026  
**Nature :** Évolution d'une application existante

---

# 1. CONTEXTE ET OBJECTIF

Dans le cadre de l'évolution de l'application existante, il est demandé d'intégrer un nouveau module permettant de contrôler automatiquement la conformité des articles entre trois sources de données :

- **Product** : informations générales des articles et statut de l'article.
- **Asten** : présence et état d'activation des articles.
- **Assortiment** : présence des références dans l'assortiment.

Le module doit appliquer des règles métier prédéfinies afin de déterminer si chaque article est conforme (**OK**) ou non conforme (**KO**).

L'objectif est de faciliter l'identification des articles qui respectent les conditions de référencement et d'activation dans les différents systèmes.

# 2. PÉRIMÈTRE DE L'ÉVOLUTION

Cette fonctionnalité doit être intégrée directement dans l'application existante.

Il ne s'agit pas de développer une nouvelle application.

Le développeur devra :

- Réutiliser les connexions aux sources de données déjà disponibles.
- Réutiliser les mécanismes existants de récupération des articles.
- Ajouter un moteur de contrôle de conformité.
- Ajouter les nouvelles règles métier.
- Intégrer les résultats dans l'interface existante.
- Préserver les fonctionnalités actuellement opérationnelles.

Aucune modification des données dans Product, Asten ou Assortiment ne doit être effectuée.

# 3. DONNÉES NÉCESSAIRES AU CONTRÔLE

Le contrôle doit exploiter les informations suivantes.

| Source | Information | Utilisation |
|---|---|---|
| Product | Référence article | Identification de l'article |
| Product | Libellé article | Affichage |
| Product | STATUS ARTICLE | Application des règles |
| Product | CODE MAK | Identification du type d'article |
| Asten | Référence article | Vérification de présence |
| Asten | État de l'article | Vérification de l'activation |
| Assortiment | Référence article | Vérification du référencement |

La référence article constitue la clé de rapprochement entre les différentes sources.

# 4. RÈGLES DE GESTION

Le moteur doit appliquer strictement les règles suivantes.

## 4.1 Cas du statut AC

Lorsque le statut de l'article dans Product est `AC` :

- L'article doit exister dans Asten.
- L'article doit être actif dans Asten.
- La référence doit être présente dans l'assortiment.

**Résultat :**

- Toutes les conditions sont respectées : OK.
- Une seule condition n'est pas respectée : KO.

## 4.2 Cas du statut ES

Lorsque le statut Product est `ES` :

- L'article est systématiquement KO.
- La présence dans Asten ou dans l'assortiment ne change pas le résultat.

## 4.3 Cas du statut NA

Lorsque le statut Product est `NA` :

- L'article est systématiquement KO.
- La présence dans Asten ou dans l'assortiment ne change pas le résultat.

## 4.4 Cas du statut NC

Lorsque le statut Product est `NC`, le résultat dépend du CODE MAK et de la présence de la référence dans l'assortiment.

**Cas 1 : CODE MAK = CC**

- Référence présente dans l'assortiment : OK.
- Référence absente de l'assortiment : KO.

**Cas 2 : CODE MAK = CD**

- Référence présente dans l'assortiment : OK.
- Référence absente de l'assortiment : KO.

**Cas 3 : CODE MAK = LD**

- L'article est systématiquement KO.
- La présence dans Asten ou dans l'assortiment ne change pas le résultat.

**Important :** pour les articles NC de type CC ou CD, la présence dans Asten et son état d'activation ne sont pas des conditions obligatoires.

## 4.5 Matrice récapitulative

| STATUS PRODUCT | CODE MAK | Asten actif | Assortiment | Résultat |
|---|---|---|---|---|
| AC | CC | Oui | Oui | OK |
| AC | CD | Oui | Oui | OK |
| AC | LD | Oui | Oui | OK |
| AC | Tous | Non | Oui | KO |
| AC | Tous | Oui | Non | KO |
| ES | Tous | Indifférent | Indifférent | KO |
| NA | Tous | Indifférent | Indifférent | KO |
| NC | CC | Indifférent | Oui | OK |
| NC | CD | Indifférent | Oui | OK |
| NC | CC | Indifférent | Non | KO |
| NC | CD | Indifférent | Non | KO |
| NC | LD | Indifférent | Indifférent | KO |

Tout statut ou CODE MAK non couvert par les règles doit être considéré comme un cas non conforme et signalé.

# 5. MOTEUR DE CONTRÔLE

Le développeur doit intégrer une fonction dédiée à la vérification de conformité.

Pseudo-code de référence :

```python
def controler_article(
    status_product,
    code_mak,
    present_asten,
    actif_asten,
    present_assortiment
):

    if status_product == "AC":
        if present_asten and actif_asten and present_assortiment:
            return "OK"
        return "KO"

    elif status_product in ("ES", "NA"):
        return "KO"

    elif status_product == "NC":

        if code_mak in ("CC", "CD") and present_assortiment:
            return "OK"

        return "KO"

    return "KO"
```

La fonction doit être indépendante de l'interface afin de pouvoir être réutilisée dans les différents traitements de l'application.

# 6. JUSTIFICATION DES RÉSULTATS

Chaque article contrôlé doit disposer d'un résultat et d'un motif explicatif.

Exemples de motifs :

| Résultat | Motif |
|---|---|
| OK | Article AC actif dans Asten et présent dans l'assortiment |
| OK | Article NC de type CC présent dans l'assortiment |
| OK | Article NC de type CD présent dans l'assortiment |
| KO | Article absent dans Asten |
| KO | Article présent mais inactif dans Asten |
| KO | Référence absente de l'assortiment |
| KO | Statut Product ES |
| KO | Statut Product NA |
| KO | Article NC avec CODE MAK LD |
| KO | Statut Product non reconnu |
| KO | CODE MAK non pris en charge |

Le motif doit correspondre à la règle qui a entraîné le résultat.

Pour les articles AC présentant plusieurs anomalies, il est préférable de restituer toutes les anomalies identifiées.

# 7. INTÉGRATION DANS L'INTERFACE EXISTANTE

Le nouveau module devra être accessible depuis l'interface actuelle.

Il devra permettre :

- De lancer le contrôle des articles.
- D'afficher le nombre total d'articles analysés.
- D'afficher le nombre d'articles OK.
- D'afficher le nombre d'articles KO.
- D'afficher le taux de conformité.
- De consulter le détail des articles.
- De consulter les motifs de non-conformité.

## 7.1 Tableau des résultats

Les résultats devront être présentés sous forme de tableau avec les colonnes suivantes :

| Colonne | Description |
|---|---|
| Référence | Référence de l'article |
| Libellé | Désignation de l'article |
| STATUS PRODUCT | AC, ES, NA ou NC |
| CODE MAK | CC, CD ou LD |
| Présent Asten | Oui / Non / N/A |
| Actif Asten | Oui / Non / N/A |
| Présent assortiment | Oui / Non / N/A |
| Résultat | OK / KO |
| Motif | Explication |

Les valeurs N/A indiquent qu'une vérification n'est pas applicable à la règle concernée.

## 7.2 Indicateurs

Ajouter les indicateurs suivants dans la partie appropriée de l'interface :

- Total des articles analysés.
- Articles conformes.
- Articles non conformes.
- Pourcentage de conformité.
- Répartition par STATUS PRODUCT.
- Répartition par CODE MAK.
- Nombre d'anomalies Asten.
- Nombre de références absentes de l'assortiment.

## 7.3 Filtres

L'utilisateur doit pouvoir filtrer les résultats selon :

- Référence article.
- Libellé.
- STATUS PRODUCT.
- CODE MAK.
- Résultat OK ou KO.
- Motif de non-conformité.

# 8. EXPORTATION DES RÉSULTATS

Le module doit permettre l'exportation des résultats du contrôle.

Formats souhaités :

- Excel (.xlsx).
- CSV.

L'export doit reprendre les mêmes informations que le tableau de consultation.

Les filtres appliqués dans l'interface doivent être pris en compte lors de l'export lorsque l'utilisateur demande l'export des résultats filtrés.

# 9. GESTION DES ERREURS TECHNIQUES

Le système doit distinguer les anomalies métier des problèmes techniques.

Exemples :

- Base Product inaccessible.
- Base Asten inaccessible.
- Source Assortiment inaccessible.
- Erreur de récupération des données.
- Référence article vide.
- Problème de rapprochement.

Une source inaccessible ne doit pas être interprétée comme une absence de référence ou un statut KO.

Dans ce cas, le contrôle doit être signalé comme incomplet ou en erreur, et les résultats concernés ne doivent pas être considérés comme validés.

# 10. TRAÇABILITÉ

Le module doit conserver les informations relatives à chaque exécution :

- Date et heure du contrôle.
- Utilisateur ayant lancé le contrôle.
- Nombre d'articles analysés.
- Nombre d'articles OK.
- Nombre d'articles KO.
- Nombre d'erreurs techniques.
- Durée du traitement.
- État de l'exécution.

L'historique permettra de consulter les résultats des contrôles précédents et de comparer les évolutions.

# 11. EXIGENCES TECHNIQUES

Le développement doit respecter les principes suivants :

1. Ne pas reconstruire l'application existante.
2. Ne pas dupliquer les connexions aux bases de données déjà disponibles.
3. Ne pas modifier les structures des bases sources.
4. Isoler les règles métier dans un module réutilisable.
5. Optimiser le rapprochement des références pour éviter les requêtes individuelles sur chaque article.
6. Prendre en charge les volumes importants de données.
7. Éviter les doublons dans les résultats.
8. Préserver les performances et les fonctionnalités existantes.
9. Respecter les conventions de développement et l'architecture déjà utilisées dans le projet.

# 12. TESTS ET RECETTE FONCTIONNELLE

Les tests suivants sont obligatoires avant validation.

| N° | Statut | MAK | Présent Asten | Actif Asten | Assortiment | Attendu |
|---|---|---|---|---|---|---|
| 1 | AC | CC | Oui | Oui | Oui | OK |
| 2 | AC | CC | Non | Non | Oui | KO |
| 3 | AC | CC | Oui | Non | Oui | KO |
| 4 | AC | CC | Oui | Oui | Non | KO |
| 5 | ES | CC | Oui | Oui | Oui | KO |
| 6 | NA | CC | Oui | Oui | Oui | KO |
| 7 | NC | CC | Non | Non | Oui | OK |
| 8 | NC | CD | Non | Non | Oui | OK |
| 9 | NC | LD | Oui | Oui | Oui | KO |
| 10 | NC | CC | Oui | Oui | Non | KO |
| 11 | NC | CD | Oui | Oui | Non | KO |
| 12 | NC | CC | Non | Non | Non | KO |

# 13. CRITÈRES DE VALIDATION

La fonctionnalité sera considérée comme terminée lorsque :

- Les règles métier sont correctement appliquées.
- Le rapprochement entre les trois sources fonctionne.
- Les résultats OK et KO sont exacts.
- Les motifs de non-conformité sont disponibles.
- Les indicateurs sont cohérents avec les résultats.
- Les filtres fonctionnent.
- Les exports sont exploitables.
- L'historique des exécutions est disponible.
- Les erreurs techniques sont correctement gérées.
- Les fonctionnalités préexistantes de l'application restent opérationnelles.

# 14. CONSIGNE FINALE AU DÉVELOPPEUR

Cette demande constitue une évolution fonctionnelle d'un projet existant.

Avant toute modification, analyser l'architecture actuelle, identifier les modules de récupération des données Product, Asten et Assortiment, puis intégrer le nouveau moteur de conformité en réutilisant au maximum les composants existants.

Ne pas créer de nouvelles connexions, tables ou interfaces lorsqu'un mécanisme équivalent existe déjà et peut être étendu.

La priorité est de garantir l'exactitude des règles métier, la fiabilité des résultats et l'absence de régression sur les fonctionnalités existantes.

**Le résultat attendu est un module de contrôle de conformité intégré à l'application actuelle, permettant d'identifier automatiquement les articles OK et KO et de justifier chaque décision.**