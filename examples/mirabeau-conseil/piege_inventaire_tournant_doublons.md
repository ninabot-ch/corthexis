---
name: piege-inventaire-tournant-doublons
description: >-
  Un inventaire tournant mal paramétré recompte les mêmes références plusieurs fois par cycle et en oublie d'autres pendant des mois — les écarts constatés sont alors du bruit, pas de la donnée. Vérifier la couverture du cycle avant d'exploiter le moindre écart.
metadata:
  type: reference
  modified: 2026-09-23
---

Un inventaire tournant découpe le stock en groupes comptés à tour de rôle. Mal
paramétré, il recompte certaines références à chaque cycle et en laisse d'autres
non comptées pendant huit à dix mois.

**Conséquence** : les écarts d'inventaire ne mesurent plus le stock, ils
mesurent la fréquence de comptage. Toute analyse de fiabilité fondée dessus est
inversée — les références « les plus fiables » sont simplement celles qu'on
compte le plus souvent.

**Rencontré chez** [[client-rochat-et-fils]] pendant la mission
ordonnancement : 340 références sur 1 200 n'avaient pas été comptées depuis
onze mois, et la charge atelier calculée dessus était fausse.

**How to apply :** avant d'exploiter un seul écart, sortir la date de dernier
comptage par référence et regarder sa distribution. Si la queue dépasse six
mois, l'inventaire tournant ne couvre pas son périmètre et il faut le dire
avant tout le reste. Le contrôle de doublons de la reprise s'applique aussi —
cf [[outil-checklist-reprise-donnees]].
