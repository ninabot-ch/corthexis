---
name: piege-export-excel-dates-suisses
description: Un export CSV depuis un ERP configuré en suisse romand sort les dates en JJ.MM.AAAA ; Excel en anglais les réinterprète en MM/JJ/AAAA sans prévenir et corrompt silencieusement tous les jours inférieurs à 13. Importer en texte, convertir ensuite.
metadata:
  type: reference
  modified: 2026-09-17
---

Un export CSV d'un ERP configuré en Suisse romande produit des dates au format
`JJ.MM.AAAA`. Ouvert dans un Excel en anglais, le 03.07.2026 devient le 7 mars.

Le piège : **seules les dates dont le jour est inférieur à 13 sont corrompues**.
Les autres sont rejetées ou laissées en texte, donc visibles. On croit avoir un
fichier propre avec quelques anomalies alors qu'on a un fichier silencieusement
faux sur un tiers des lignes.

**How to apply :** ne jamais double-cliquer un CSV client. Passer par
« Données → À partir d'un fichier texte », déclarer la colonne en **Texte**, puis
convertir explicitement. Et vérifier systématiquement le nombre de lignes par
mois : une bascule jour/mois se voit immédiatement sur la répartition.
