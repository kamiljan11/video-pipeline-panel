# ADR-0005 — Biblioteka animacji do kart: GSAP w obecnym rendererze, nie Remotion ani OpenChatCut

Data: 2026-10-22 | Status: przyjęte (pilot jeszcze nie zbudowany) | Drzwi: dwukierunkowe | Przegląd: 2026-12-01

**Kontekst:** Kamil (2026-10-22) widział reklamy „AI edytuje wideo z motion graphics” i pyta, na ile nasz system jest dopracowany. Mamy 7 typów kart (kw, list, lower, slide, chap, cta, end). `gfx/render.mjs` składa je w HTML+CSS. Renderuje je Chromium z Playwrighta klatka po klatce: zatrzymuje animacje (`document.getAnimations()`), ustawia `currentTime`, robi zrzut i składa ProRes 4444 z alfą. Animacje to proste wjazdy CSS. Brakuje animowanej typografii, liczników „0 → 95%”, wykresów rysowanych w czasie i przejść.

**Kryteria:**
1. Licencja pozwala na filmy firmowe na YouTube.
2. Zero zmian w tym, jak pipeline składa film (karty dalej jako .mov z alfą).
3. Mała powierzchnia: jedna zależność, bez nowego frameworka.
4. Determinizm klatek, bo ta sama karta musi dać te same klatki.
5. Bezpieczeństwo: znany wydawca, rejestr npm, bez binarek.

| Kryterium (waga %) | A: GSAP w `render.mjs` | B: Remotion (React) | C: OpenChatCut (edytor + MCP) | D: Lottie (lottie-web) | E: zostać przy CSS |
|---|---|---|---|---|---|
| Licencja (25) | 5: darmowy, także komercyjnie i z wtyczkami (SplitText, MorphSVG…); wyłączenie tylko dla konkurencji edytora Webflow | 4: darmowy dla firm do 3 osób, potem od $25/mies./stanowisko; licencja zmieni się w 5.0 | 3: AGPL; dla samego użytku OK, ale wplatanie w nasz kod wymusza AGPL | 5: MIT | 5 |
| Zgodność z pipeline (25) | 5: ta sama strona HTML, w pętli klatek dochodzi `gsap.globalTimeline.seek(t)` | 2: przepisanie kart na React + własny renderer Remotion | 1: inny model: timeline zamiast naszego planu | 4: osadzenie w HTML, `goToAndStop(klatka)` | 5 |
| Możliwości (20) | 5: timeline, SplitText (litera po literze), liczniki, morfing SVG | 5 | 3: szablony + efekty, ale montaż, nie projekt kart | 4: gotowe animacje z LottieFiles (ikony, ilustracje) | 2 |
| Dojrzałość (15) | 5: wieloletni standard, Webflow | 5 | 2: v0.1–0.2, Linux tylko z paczki społeczności (AUR), niepodpisany | 5 | 5 |
| Koszt wdrożenia (15) | 4: jeden typ karty jako pilot | 1 | 2 | 3 | 5 |
| **Suma** | **4,85** | 3,4 | 2,2 | 4,25 | 4,25 (nie spełnia celu) |

Źródła (odczyt 2026-10-22):
- https://webflow.com/blog/gsap-becomes-free i https://gsap.com/blog/3-13/: cały GSAP darmowy, także komercyjnie.
- https://gsap.com/community/standard-license/: wyłączenie dotyczy narzędzi konkurujących z animacjami Webflow.
- https://github.com/remotion-dev/remotion/blob/main/LICENSE.md i https://www.remotion.dev/pricing: darmowy dla osób, firm do 3 pracowników i non-profit.
- https://github.com/0xsline/OpenChatCut i https://aur.archlinux.org/packages/openchatcut: AGPL, wczesna wersja, na Linuksie paczka społeczności.

**Decyzja:** A, czyli GSAP jako jedna zależność npm w istniejącym rendererze. Zaczynamy od pilota: jeden nowy typ karty `count` (liczba odliczana do wartości plus podpis), np. „95%”, „5 h / tydzień”.

Warunki przed instalacją:
- Linia bezpieczeństwa: źródło npm `gsap`, wydawca GreenSock/Webflow, sprawdzone podpisy i `npm audit`, brak skryptów `postinstall`.
- Test determinizmu: dwa rendery tej samej karty dają identyczne klatki.

D (Lottie) zostaje jako drugi krok na animowane ikony, gdy pilot GSAP się sprawdzi. B i C odrzucone: przepisanie albo zmiana modelu pracy nie daje widzowi nic, czego A nie da taniej.

**Konsekwencje:**
- Nowy typ karty to nowy klucz w `gfx_specs`. Stare specyfikacje renderują się bez zmian, bo strażnik `tests/test_pipeline_compat.py` w panelu dostaje test karty `count`.
- Wyłącznik: bez kart `count` GSAP nie jest ładowany do strony.
- Rollback = usunięcie typu karty i zależności.
