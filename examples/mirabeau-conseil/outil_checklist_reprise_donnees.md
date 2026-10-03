---
name: outil-checklist-reprise-donnees
description: >-
  Checklist de reprise de données client, à passer avant toute analyse — sept contrôles, dont le comptage de lignes, le format des dates et la détection des doublons. Une analyse sur un fichier non contrôlé est une analyse fausse qu'on présentera avec assurance.
metadata:
  type: reference
  modified: 2026-09-23
---

Sept contrôles, dans l'ordre, sur tout fichier reçu d'un client. Aucun ne prend
plus de deux minutes.

1. **Nombre de lignes** de l'export comparé au nombre annoncé par l'ERP.
2. **Format des dates** — cf [[piege-export-excel-dates-suisses]], c'est le
   piège qui nous a coûté le plus cher.
3. **Séparateur décimal** — virgule ou point, et jamais les deux.
4. **Doublons** sur la clé métier, pas sur la ligne entière.
5. **Valeurs nulles** comptées par colonne, avant analyse.
6. **Bornes de dates** — un export « 12 derniers mois » qui commence au 3 du
   mois en cache souvent un filtre involontaire.
7. **Champs personnalisés** de l'ERP, qui ne veulent pas dire ce que leur nom
   dit — cf [[piege-sage-x3-champs-personnalises]].

**Why :** une analyse sur un fichier non contrôlé n'est pas une analyse fragile,
c'est une analyse fausse, et on la présente avec la même assurance qu'une vraie.

**Sur le stockage** : le fichier reste sur le poste, chiffré, et disparaît à la
clôture — cf [[rgpd-nlpd-donnees-client]].
