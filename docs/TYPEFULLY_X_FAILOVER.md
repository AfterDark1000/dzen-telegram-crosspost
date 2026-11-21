# ХПИИ — резервный X-публикатор Typefully

**Основной маршрут:** Дзен → GitHub Actions → Buffer → @iipike_ai.
**Резерв:** если в x_editorial_queue.json подтверждён Buffer HTTP 429 и ещё действует buffer_cooldown_until — Typefully API v2. Другие ошибки Buffer не дают разрешения на резерв: результат отправки может быть неизвестен.

## До включения

1. Войти на https://typefully.com и подключить **ровно @iipike_ai** в social set. Проверить бесплатный лимит (официальный Free: до 10 постов за месяц).
2. Typefully → Settings → API → Create new API key. Ключ должен позволять READ/WRITE/PUBLISH именно своего social set. **Не вставлять ключ в чат или публичный файл.**
3. В GitHub репозитории AfterDark1000/dzen-telegram-crosspost в **Settings → Secrets and variables → Actions**:
   - Вкладка **Secrets**, новый repository secret: TYPEFULLY_API_KEY (сам ключ).
   - Вкладка **Variables**, новая repository variable: TYPEFULLY_SOCIAL_SET_ID (числовой ID social set, можно узнать в режиме Development mode Typefully → Settings → API).
   - Вкладка **Variables**, TYPEFULLY_FAILOVER_ENABLED со значением **true** только после проверки связи и осознанного включения. Пока отсутствует/false — никаких запросов к Typefully.
4. Проверить первый разрешённый проход и ссылку на реальный пост в X. Изменения очереди сохраняются штатным state_checkpoint, а не отдельной небезопасной записью.

## Правила переключения

- Один исполнитель за запуск: x_dispatch.py выбирает Buffer или Typefully. Периодичность X: окна UTC 00/12, Telegram и Threads не замедляются.
- Typefully работает **только** при зафиксированном Buffer 429, либо читает уже принятые Typefully-посты для сверки. В отсутствие активной блокировки Buffer — основной маршрут.
- Берёт только одобренный текст из x_editorial_queue.json, только точное совпадение с публичным заголовком Дзена и возраст статьи не старше 72 часов. Горячие/чужие/неодобренные материалы не затрагиваются.
- Не больше **одного Typefully-поста в 24 часа** и **8 за последние 30 дней** по консервативному подсчёту Typefully (официальный Free — 10/месяц; отдельные ручные публикации тоже могут расходовать лимит).
- Первая версия резервного канала — **только текст**, без обложки. Buffer сохраняет свою схему чередования.
- POST /drafts с publish_at="now" не равен доставке: Typefully может отвечать status=draft и publish_state=in_progress. Успехом считается только status=published с настоящей ссылкой https://x.com/iipike_ai/status/… . До того запись остаётся approved с ID попытки; Buffer не может отправить её параллельно.
- При таймауте/непонятном ответе: записать typefully_attempt_unknown, **никогда автоматически не повторять отправку**. Идентичная draft_title / текст в Typefully — сигнал для сверки, не для повторного POST. Если результат не подтверждён — ручная проверка.
- Никакого автоматического догоняющего выпуска старых новостей и никакой попытки обходить Buffer API cooldown.
- При возвращении Buffer сначала сверяем незавершённую отправку Typefully; только после этого первичный маршрут имеет право возобновиться.

## Отладка

Workflow: .github/workflows/crosspost.yml, шаг **Publish approved X via Buffer or guarded Typefully fallback**.

Для диагностики используются сообщения X TYPEFULLY, X ROUTE HELD. Секреты не печатаются. При любых ошибках отправителя общий workflow продолжает остальные платформы; последний state_checkpoint сохраняет защитные метки.

Тесты: python3 -m unittest discover -s tests -p 'test_typefully_x_failover.py' -v (offline, без API).

API: https://typefully.com/docs/api ; тарифы https://typefully.com/pricing .
