# Прямой доступ к удалённому MCP по SSH

Если MCP слушает только loopback сервера, SSH-туннель позволяет подключить
Geminka без Cloudflare и reverse proxy, не публикуя MCP в интернете.
Используйте уже настроенный SSH-ключ; не сохраняйте пароль в проекте или unit.

Пример: локальный порт 18765 перенаправляется непосредственно в MCP на сервере:

```sh
ssh -NT -o BatchMode=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:18765:127.0.0.1:8765 user@server
```

В локальном `.env`:

```dotenv
TG_COLLECTOR_MCP_ENABLED=true
TG_COLLECTOR_MCP_URL=http://127.0.0.1:18765/mcp
TG_COLLECTOR_MCP_HOST=127.0.0.1:8765
# TG_COLLECTOR_MCP_TOKEN остаётся действующим секретным токеном MCP.
```

`TG_COLLECTOR_MCP_HOST` задаёт HTTP Host удалённого loopback-сервиса, чтобы
проверка DNS rebinding MCP не отклоняла другой локальный порт туннеля.
Override разрешён только для loopback HTTP URL и loopback Host с портом;
для обычного HTTPS URL оставьте переменную пустой. TLS не отключается:
внешний участок соединения шифруется SSH, HTTP используется внутри loopback.

Для постоянной работы запустите такую SSH-команду отдельным user-systemd
сервисом с `Restart=always` и `RestartSec=5`, а сервису бота добавьте
`Wants=`/`After=` на туннель. Настройки экземпляра и адреса сервера находятся
в локальных unit/drop-in файлах, не в репозитории. Изменение `.env` требует
перезапуска бота; активную генерацию предварительно завершите.

Туннель обходит лимитеры промежуточных прокси, но не лимиты самого приложения
или базы данных. Это не отменяет необходимость ограничивать лишние MCP-вызовы.
