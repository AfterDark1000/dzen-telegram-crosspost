# ХПИИ: UTM-метки входящего трафика iipike.ru

Цель: измерять **входящие переходы** из наших площадок на iipike.ru в Яндекс Метрике №114100188. Это не UTM для ссылок с сайта на соцсети.

## Автоматическая разметка

- Telegram `@iipike`: у новой статьи появляется ссылка «Читать на сайте ХПИИ →», только если запись Дзена имеет точно совпадающий подтверждённый `dzen_url` / `site_first_dzen_url` в публичном `https://iipike.ru/news.json`. Ссылка на оригинал в Дзене сохраняется. Если новый текст превысит лимит подписи фотографии в 1024 символа, дополнительная ссылка опускается, чтобы не потерять обложку.
- MAX: сообщения копирует внешний Repostbot из Telegram. Поэтому **тот же URL попадает в MAX** и отмечается `utm_source=telegram_max` как **совместный трафик Telegram + MAX**, а не ошибочно «Telegram». Разделить эти источники достоверно без отдельного изменения ссылок репостером невозможно.
- VK: к полному тексту новых постов добавляется ссылка на соответствующую статью сайта с `utm_source=vk`. Полный текст не сокращается ради ссылки; при нехватке лимита дополнительная ссылка опускается.
- Когда `news.json` недоступен или связь с Дзеном не установлена однозначно, обе публикации идут по прежней схеме; сайт не блокирует кросспостинг.

## Общая схема

Статьи: `https://iipike.ru/articles/<slug>/?utm_source=<source>&utm_medium=social&utm_campaign=hpii_articles&utm_content=<slug>`

Профиль / главная: `https://iipike.ru/?utm_source=<source>&utm_medium=social&utm_campaign=hpii_profile`

Значения `source`: `vk`, `telegram_max`, `dzen`, `threads`, `x`, `youtube`, `instagram`, `max` (последнее только для самостоятельной публикации непосредственно в MAX).

**Готовые ссылки на главную для ручного размещения:**

- Дзен: https://iipike.ru/?utm_source=dzen&utm_medium=social&utm_campaign=hpii_profile
- Threads: https://iipike.ru/?utm_source=threads&utm_medium=social&utm_campaign=hpii_profile
- X: https://iipike.ru/?utm_source=x&utm_medium=social&utm_campaign=hpii_profile
- YouTube: https://iipike.ru/?utm_source=youtube&utm_medium=social&utm_campaign=hpii_profile
- Instagram: https://iipike.ru/?utm_source=instagram&utm_medium=social&utm_campaign=hpii_profile
- MAX (только если размещено напрямую): https://iipike.ru/?utm_source=max&utm_medium=social&utm_campaign=hpii_profile

Дзен публикуется вне этого GitHub-конвейера. Его статьи и профиль, а также утверждённые тексты Threads и X, **не редактируются автоматически** ради UTM: нужный URL добавляется редактором на соответствующей площадке после проверки длины и редакционной целостности.

## Метрика

`Отчёты → Источники → Метки UTM` покажет визиты по `utm_source`, `utm_campaign` и `utm_content`. Источник `telegram_max` объединён; это сознательно, чтобы не преувеличивать отдельный канал.

Не тестировать эти ссылки из рабочего браузера с фильтром «Не учитывать мои визиты»: в Метрике может не появиться тестовая конверсия.

**Ограничения:** уже опубликованные сообщения не меняются; UTM-событие отражает переход, а не подписку или полное прочтение. Источник может теряться при промежуточных редиректах или перепубликации ссылок.
