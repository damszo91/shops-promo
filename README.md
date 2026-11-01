# Shops Promo

Robot sam szuka przecenionych produktów w sklepach internetowych (na razie w Sinsay). Skanuje 3 razy dziennie i pokazuje wyniki na stronie, na której są tylko przeceny.

**Co widzisz na stronie:** rabat w %, cenę obecną i przekreśloną, najniższą cenę z 30 dni oraz oznaczenia:
- **NOWE** – przecenione w ciągu ostatnich 24 godzin,
- **↓ dalej tanieje** – cena spadła od poprzedniego skanu,
- **było X zł** – robot widział już niższą cenę, więc rabat może być naciągany.

Są też filtry (kategoria, wyszukiwarka, minimalny rabat, maksymalna cena) i ulubione ♡.

---

## Windows – uruchomienie dwuklikiem

1. Kliknij dwukrotnie **URUCHOM.bat**.
   - Jeśli Windows pokaże „System Windows ochronił ten komputer”, kliknij **Więcej informacji → Uruchom mimo to**.
   - Jeśli nie masz Pythona, skrypt sam go zainstaluje. Potem zamknij okno i kliknij URUCHOM.bat jeszcze raz.
2. Pierwsze uruchomienie trwa kilka minut, bo instalują się potrzebne składniki. Potem robot skanuje Sinsay i otwiera stronę z okazjami.
3. Przy pierwszym uruchomieniu włącza się też **automatyczne skanowanie o 7:00, 13:00 i 19:00**, w tle i bez okienek, o ile komputer jest wtedy włączony.

Żeby zobaczyć najnowsze okazje, otwórz plik **docs\index.html** (warto dodać go do zakładek w przeglądarce).

- **Wyłączenie automatu:** kliknij `WYLACZ-harmonogram.bat`.
- **Ręczny skan:** kliknij ponownie `URUCHOM.bat`.
- **Log ostatniego automatycznego skanu:** plik `ostatni-skan.log`.

---

## Wersja online (GitHub) – strona dostępna też na telefonie

1. Na github.com załóż konto i utwórz repozytorium **Public**, np. `shops-promo`.
2. Wgraj wszystkie pliki: *uploading an existing file*. Folder `.github` też musi trafić do repozytorium.
3. Wejdź w *Settings → Pages* i ustaw *Branch*: `main` oraz folder `/docs`.
4. Wejdź w *Actions → Shops Promo - skan → Run workflow*.
5. Strona będzie pod adresem `https://TWOJA-NAZWA.github.io/shops-promo/`. Skanowanie odbywa się 3 razy dziennie, ok. 7:00, 13:00 i 19:00.

---

## Gdy coś nie działa

Jeśli robot nie znajdzie produktów, **nie czyści strony**, tylko zapisuje zrzuty ekranu w folderze `debug`. Wyślij je Claude'owi, a skaner zostanie poprawiony.

Najczęstsza przyczyna to zmiana adresu wyprzedaży przez sklep. Wtedy skopiuj adres listy przecenionych produktów do `config.json`:
```json
"start_urls": ["https://www.sinsay.com/pl/pl/...adres-wyprzedazy..."],
```

## Ustawienia (`config.json`)

| Pole | Znaczenie |
|---|---|
| `start_urls` | listy produktów do skanowania; puste = robot sam szuka działów wyprzedaży |
| `min_discount_percent` | od jakiego rabatu produkt trafia na stronę (domyślnie 10%) |
| `max_listing_pages` | ile działów maksymalnie skanować |
| `delay_seconds` | przerwa między stronami, żeby nie obciążać sklepu |
