# Meegeleverde bibliotheek

`xlsx.full.min.js` is [SheetJS](https://sheetjs.com) Community Edition, versie 0.20.3, onder de Apache-licentie 2.0 (zie `LICENSE-SheetJS-Apache-2.0.txt`). De tool laadt dit bestand pas als je een Excel-bestand importeert of exporteert.

Het bestand komt uit het npm-pakket `@e965/xlsx@0.20.3`. Dat pakket is een ongewijzigde kopie van de officiële SheetJS-release. SheetJS zelf publiceert zijn nieuwere versies niet meer op npm.

Controleer het bestand met:

    shasum -a 256 vendor/xlsx.full.min.js

Verwacht resultaat:

    cc015130aa8521e7f088f88898eba949ccdcbfb38df0bd129b44b7273c3a6f41  vendor/xlsx.full.min.js
