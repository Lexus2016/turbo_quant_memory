# Phase 9: Project-Scoped Encrypted Secrets Vault — Context

**Gathered:** 2026-05-25
**Status:** Ready for execution
**Triggered by:** Repeated cross-session loss of agent-visible credentials (SSH, DB DSN, API tokens). Pasting them back into every fresh session wastes tokens and time, and external password managers (Keychain, 1Password, `pass`) require per-machine setup that we cannot assume.

**Викликано:** Повторна втрата доступових даних між сесіями (SSH, DB DSN, API-ключі). Перевставляти їх у кожну нову сесію марнує токени і час, а зовнішні менеджери паролів (Keychain, 1Password, `pass`) вимагають налаштування на машині, на яке ми не маємо права розраховувати.

## Phase Boundary

Ship a per-project encrypted secrets store that lives next to existing project memory under `~/.turbo-quant-memory/projects/<project_id>/secrets/`, is invisible to all retrieval paths, and survives daemon restarts and machine reboots.

Випустити per-project зашифроване сховище секретів, яке живе поряд із поточною project memory у `~/.turbo-quant-memory/projects/<project_id>/secrets/`, не видиме жодному retrieval-шляху і переживає перезапуски daemon'а та reboot машини.

- Store credentials encrypted at rest with per-project master keys.
- Reject any read path that could leak values into `semantic_search`, `hydrate`, or `lint_knowledge_base`.
- Auto-provision an empty vault directory for every existing project on first daemon start after the upgrade (migration).
- Expose four narrow MCP tools (`set_secret`, `get_secret`, `list_secrets`, `delete_secret`) and bump tool count `14 -> 18`.

- Зберігати credentials зашифровано at-rest з per-project майстер-ключами.
- Заблокувати будь-який read-шлях, що міг би просочити значення у `semantic_search`, `hydrate` або `lint_knowledge_base`.
- При першому старті daemon'а після оновлення автоматично створювати порожній vault-каталог для кожного існуючого проєкту (міграція).
- Додати чотири вузькі MCP-інструменти (`set_secret`, `get_secret`, `list_secrets`, `delete_secret`) і підняти tool count з `14` до `18`.

## Locked Decisions

- **Scope is project-only.** No global secrets. `set_secret` does not take a `scope` parameter; it is implicit. Compromise of one project's vault never affects another.
- **Скоуп тільки project.** Глобальних секретів немає. `set_secret` не приймає `scope`; він неявний. Компроміс одного project-vault'а ніколи не зачіпає інший.

- **Threat-model floor:** protect against accidental backup (Time Machine, rsync, iCloud), share-screen leak, and accidental `git add`. Defeating a compromised root user or a live attacker on the daemon process is explicitly OUT of scope.
- **Threat-model floor:** захист від випадкового бекапу (Time Machine, rsync, iCloud), share-screen-витоку і випадкового `git add`. Захист проти скомпрометованого root-користувача або live-атаки на daemon — явно ПОЗА скоупом.

- **Master-key resolution priority (env wins as the explicit user choice; no interactive fallback):**
  1. Env var `TQMEMORY_SECRETS_PASSPHRASE` if set -> Argon2id-derived per-project key with salt = `sha256("tqv-salt-v1:" + project_id)`. Env is explicit user intent; it always wins so headless setups are deterministic.
  2. Existing OS keyring entry `service=turbo-quant-memory, account=secrets-master-{project_id}` via Python `keyring` lib. On macOS this is Keychain and auto-unlocks at login -> reboot-transparent.
  3. Keyring auto-bootstrap: if neither (1) nor (2) is available but keyring is writable, generate 32 random bytes (`secrets.token_bytes`) and store under (2)'s service/account. First `set_secret` works on macOS with zero manual setup.
  4. Hard-fail with an actionable setup message. No interactive prompt cached in daemon memory — that would silently die on reboot, which the user marked as a hard constraint ("втрата ключа смертельно").

- **Priority розв'язання майстер-ключа (env перемагає як явний вибір користувача; інтерактивного fallback'а немає):**
  1. Env var `TQMEMORY_SECRETS_PASSPHRASE` якщо встановлена -> Argon2id-derived per-project ключ із сіллю `sha256("tqv-salt-v1:" + project_id)`. Env — це явна воля користувача; вона завжди перемагає, щоб headless-конфігурації були детермінованими.
  2. Існуюче OS keyring entry `service=turbo-quant-memory, account=secrets-master-{project_id}` через Python-бібліотеку `keyring`. На macOS це Keychain і він auto-unlock'ається при логіні -> переживає reboot прозоро.
  3. Keyring auto-bootstrap: якщо ні (1), ні (2) недоступне, але keyring writable — генеруємо 32 random байти (`secrets.token_bytes`) і кладемо під ту ж пару service/account. Перший `set_secret` на macOS працює без жодного ручного налаштування.
  4. Hard-fail зі зрозумілим setup-повідомленням. Жодного інтерактивного prompt'у з кешем у пам'яті daemon'а — він би тихо помер на reboot, а користувач явно зафіксував це як hard-обмеження ("втрата ключа смертельно").

- **Crypto = AES-256-GCM** through the `cryptography` package; Argon2id from `argon2-cffi`. No bespoke crypto. The vault file is a single-blob `vault.tqv` with a 12-byte random nonce + GCM tag prepended; KDF parameters and key-resolution mode live in a sidecar `meta.json`.
- **Криптографія = AES-256-GCM** через пакет `cryptography`; Argon2id з `argon2-cffi`. Жодної саморобної криптографії. Vault-файл — single-blob `vault.tqv` з 12-байтним random nonce + GCM tag спереду; KDF-параметри і key-resolution mode зберігаються в sidecar `meta.json`.

- **Strict isolation from retrieval.** `semantic_search`, `hydrate`, `lint_knowledge_base`, embedding indexer, and BM25 FTS index MUST NOT traverse the `secrets/` subdirectory. The exclusion is enforced both by ingester guards (refuse `secrets/` paths) and by directory layout (vault lives outside any indexed root by default).
- **Жорстка ізоляція від retrieval.** `semantic_search`, `hydrate`, `lint_knowledge_base`, embedding-indexer і BM25 FTS-індекс НЕ ПОВИННІ обходити підпапку `secrets/`. Це підкріплюється двічі: guard'ами в індексаторі (відмова на `secrets/`-шляхах) і layout'ом (vault за замовчанням лежить поза індексованими root'ами).

- **MCP response shape.** Secret values are returned in a dedicated `secret_value` field, not interpolated into descriptive `summary`/`message` text. Per-project audit log (`projects/<project_id>/secrets/audit.jsonl`) records every access by `(timestamp, action, name)`; `project_id` is implicit from the path and the value is never logged.
- **Форма MCP-відповіді.** Значення секретів повертаються у виділеному полі `secret_value`, ніколи не вшиваються в описові `summary`/`message`. Per-project audit-log (`projects/<project_id>/secrets/audit.jsonl`) пише кожен доступ як `(timestamp, action, name)`; `project_id` неявний з шляху, а значення ніколи не логується.

- **Migration (`Subsystem.SECRETS`, v0 -> v1).** On first daemon start with the new version, enumerate `~/.turbo-quant-memory/projects/*` and provision an empty `secrets/` directory + empty `vault.tqv` + initial `meta.json` for each existing project. Idempotent. Per the user's hard requirement: indexing per project happens immediately at upgrade time, not lazily on first `set_secret`.
- **Міграція (`Subsystem.SECRETS`, v0 -> v1).** На першому старті daemon'а з новою версією: пробігтись по `~/.turbo-quant-memory/projects/*` і створити для кожного наявного проєкту порожній каталог `secrets/` + порожній `vault.tqv` + початковий `meta.json`. Ідемпотентно. Згідно з hard-вимогою користувача: індексація per project відбувається одразу при upgrade, а не лінько при першому `set_secret`.

## Dependencies

| Package | Version | License | Purpose |
|---|---|---|---|
| `keyring` | >=24 | MIT | OS-keyring abstraction (Keychain/libsecret/Credential Manager) |
| `cryptography` | >=42 | Apache-2.0 / BSD | AES-256-GCM primitives |
| `argon2-cffi` | >=23 | MIT | Memory-hard KDF for env-passphrase mode |

All pure-Python wheels with native CPython extensions. No new system-level dependencies.

Усі — pure-Python wheels із native CPython-розширеннями. Жодних нових системних залежностей.

## Out of Phase (deferred)

- Master-key rotation tool (`rotate_master_key(project_id)`) — design noted, ship in a follow-up.
- Secret export / cross-machine sync — explicitly out; user re-enters on a fresh machine.
- Pre-commit / git-add hook that blocks accidental `vault.tqv` outside `~/.turbo-quant-memory/` — documentation note, not enforced by us.

- Інструмент ротації майстер-ключа — дизайн зафіксовано, поставка в follow-up.
- Експорт секретів / cross-machine sync — явно поза скоупом; на новій машині користувач вводить заново.
- Pre-commit / git-add хук, що блокує випадковий `vault.tqv` поза `~/.turbo-quant-memory/` — лише як документація, ми це не enforce'имо.
