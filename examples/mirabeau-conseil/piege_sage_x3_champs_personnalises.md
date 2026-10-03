---
name: piege-sage-x3-champs-personnalises
description: >-
  Dans un Sage X3 personnalisé, les champs libres ont été détournés de leur usage au fil des années : chez ARVA, le champ « date de péremption » porte la date de dernier inventaire depuis 2019. Toujours faire confirmer le sens de chaque champ par un utilisateur, jamais par sa documentation.
metadata:
  type: reference
  modified: 2026-09-23
---

Un ERP personnalisé depuis dix ans a des champs qui ne veulent plus dire ce que
leur nom dit. Ce n'est pas un bug, c'est une sédimentation : un champ libre a
été réquisitionné un jour pour un besoin urgent, et personne ne l'a renommé.

**Le cas qui a servi de leçon** : chez [[client-arva-negoce]], le champ
`Date de péremption` porte en réalité la date du dernier inventaire physique
depuis une reprise en 2019. Une analyse des péremptions fondée dessus a produit
trois semaines de conclusions absurdes avant qu'un préparateur ne le signale en
passant.

**How to apply :** pour chaque champ utilisé dans une analyse, faire confirmer
son sens **par un utilisateur qui le saisit**, jamais par la documentation ni
par l'intégrateur. La question qui marche : « montrez-moi la dernière fois que
vous avez rempli ça ».

**Corollaire** : c'est le contrôle n° 7 de la reprise de données, et il est le
plus souvent sauté — cf [[piege-export-excel-dates-suisses]] pour le n° 2, qui
est du même genre.
