# ADR-0004 — Karta źródła `web`: przytaczana strona/PDF w oknie przeglądarki, przewinięta do cytatu

Data: 2026-10-22 | Status: przyjęte | Drzwi: dwukierunkowe | Przegląd: po pierwszym filmie z kartą `web`

**Kontekst:** Kamil (2026-10-22): gdy przytaczamy badanie albo stronę, „skoro o czymś mówimy, to można to pokazać” —
nagranie scrollowania albo zrzut na „ekraniku”. Dotąd źródło było tylko podpisem na slajdzie (`slide.src`, np. MIT NANDA).
Widz nie widzi, że cytat istnieje; film o „95 % wdrożeń AI” opiera się na raporcie, którego nie pokazuje.

**Kryteria:** (1) widz widzi prawdziwe źródło i dokładne zdanie (dowód, nie ozdoba); (2) jakość 1440p i ten sam wygląd co
reszta grafik; (3) powtarzalne: przebudowa filmu nie zależy od sieci ani od zmian strony; (4) bez kont i kosztów; (5) zapis
specu z Syncthinga nie może zamienić renderu w czytnik lokalnych usług/plików.

| Kryterium (waga %) | A: Kamil nagrywa ekran sam (OBS) | B: zrzut strony + animacja w karcie | C: nagranie wideo przeglądarki (Playwright `recordVideo`) |
|---|---|---|---|
| Dowód, zdanie widoczne (30) | 3: zależy od ręki, bez zaznaczenia | 5: zaznaczenie dokładnie na zdaniu | 4: zaznaczenie trzeba dorysować |
| Jakość i spójność (25) | 3: inny kadr/kolory | 5: ten sam szablon (`template.html`) | 2: niski fps, przycięcia, doczytywanie |
| Powtarzalność (20) | 2: nowe nagranie przy zmianie | 5: zrzut raz, render bez sieci | 2: każda przebudowa = nowa sesja strony |
| Praca Kamila (15) | 1: każdy cytat ręcznie | 5: adres + zdanie w specu | 5 |
| Bezpieczeństwo (10) | 5 | 4: https + blokada adresów prywatnych | 4: jak B |
| **Suma** | 2,7 | 4,9 | 3,3 |

**Decyzja:** B. `gfx/webshot.mjs` robi zrzut (Chrome 1200 px, skala 2) albo strony PDF (`pdftoppm` 216 dpi) i szuka zdania
`find` (porównanie po samych literach/cyfrach, więc cudzysłowy, myślniki i łamania nie przeszkadzają); `gfx/render.mjs`
rysuje kartę: okno przeglądarki, przewinięcie do zdania (40 % wysokości okna), marker linia po linii, podpis z datą zrzutu.
Twarz w rogu jak w `slide` (`gfx_types.PIP`). Wyjście awaryjne: własny zrzut `img` (+ `box`), gdy strona blokuje roboty —
blokad nie obchodzimy (żadnego udawania przeglądarki). Banery cookies ukrywane CSS-em, nigdy klikane (żadnej zgody w imieniu Kamila).

**Konsekwencje:** pierwsza sieć w renderze grafik (tylko gdy jest karta `web` bez zrzutu). Brak zdania / HTTP 4xx = film
się nie renderuje (`FAILED webshot <id>` w `gfx.log`), zgodnie z zasadą fail-fast. Zrzut to stan strony z dnia zrzutu
(podpis „stan na …”); nowy zrzut = usunąć `work/gfx/web/<klucz>.*`. Tylko 16:9 (shorty: wersja z telefonem później).
Prawo cytatu: krótki fragment z podanym źródłem. Moment w gramatyce montażu: karta jest projektowana ręcznie jak `slide`
(`auto_gfx.long = []`), więc `style/grammar.json` się nie zmienia.

**Metryka:** filmy z przytoczonym źródłem mają kartę `web` przy pierwszym przytoczeniu; liczba ręcznych zrzutów `img`
(jeśli > połowa kart, wrócić do opcji C albo dodać obsługę logowania przez Kamila).

**Wykonanie / rollback:** `gfx/webshot.mjs`, `gfx/webshot_lib.mjs` (+ `npm test`), `gfx/render.mjs` (typ `web`),
`gfx_types.py`; panel: typ `web` w edytorze grafik (`planops._web_fields`). Rollback = usunąć karty `web` ze specu.
