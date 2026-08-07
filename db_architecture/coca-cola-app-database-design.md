# Coca-Cola Gamified Loyalty App — Database Design (v1)

Target engine: PostgreSQL · Target ORM (next step): SQLAlchemy · Normalization: 3NF

This is a design document, not SQL. Review it, tell me what to change, and once we're aligned I'll generate the SQLAlchemy models from it.

---

## 1. Design Approach

Three architectural decisions shape everything below:

1. **Coins are a ledger, not a counter.** `users.coin_balance` is a cached/denormalized value for fast reads, but the *source of truth* is an append-only `coin_transactions` table. Every coin movement (QR redemption, purchase, vote, prize payout) is a row. This gives you full auditability and makes "every coin transaction should be traceable" a hard guarantee, not a hope.
2. **Enums become lookup tables where they're likely to grow, real PostgreSQL ENUMs where they're closed and stable.** Competition status, transaction type, and notification type are lookup tables (FK'd) because your "Future Features" list implies new statuses/types over time (e.g., new notification kinds for daily challenges). Slot type (hair/hat/top/etc.) is a native ENUM — six body slots is a stable, closed set unlikely to change without an app redesign anyway.
3. **Junction tables everywhere there's a many-to-many or an ownership/state relationship**, rather than array columns or JSON blobs, so constraints and indexes can do the enforcement instead of application code.

---

## 2. Entities

### 2.1 `users`
Core identity record.

| Column | Type | Constraints |
|---|---|---|
| user_id | UUID | PK |
| username | CITEXT(30) | UNIQUE, NOT NULL, indexed |
| email | CITEXT(255) | UNIQUE, NOT NULL, indexed |
| password_hash | VARCHAR(255) | NULL (nullable to support OAuth-only accounts) |
| profile_picture_url | TEXT | NULL |
| biography | VARCHAR(280) | NULL |
| coin_balance | INTEGER | NOT NULL, DEFAULT 0, CHECK (coin_balance >= 0) — **cached**, derived from `coin_transactions` |
| winning_streak | INTEGER | NOT NULL, DEFAULT 0 |
| is_active | BOOLEAN | NOT NULL, DEFAULT true (soft delete) |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

**Why `CITEXT` instead of `VARCHAR`?** Without it, `John` and `john` are two distinct values — they'd pass the `UNIQUE` constraint as separate accounts, and a search for `john` wouldn't find `John`. The `citext` extension (`CREATE EXTENSION citext;`) makes comparisons, uniqueness, and indexing case-insensitive automatically, so you don't have to remember to wrap every query in `LOWER()`. Applied to both `username` and `email`.

**Why not store followers/wins/losses as columns?** They're derivable (COUNT from `follows`/`competitions`) and would drift out of sync. See section 7 — a `user_statistics` table is the designated scaling path once computing these live gets expensive; not needed for MVP.

### 2.2 `auth_providers`
Supports future Google/Apple login without touching `users`.

| Column | Type | Constraints |
|---|---|---|
| auth_provider_id | UUID | PK |
| user_id | UUID | FK → users, NOT NULL |
| provider | ENUM('local','google','apple') | NOT NULL |
| provider_user_id | VARCHAR(255) | NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

UNIQUE (provider, provider_user_id) — one external identity maps to exactly one account.

### 2.3 `avatars`
One avatar per user (1:1).

| Column | Type | Constraints |
|---|---|---|
| avatar_id | UUID | PK |
| user_id | UUID | FK → users, UNIQUE, NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

### 2.4 `avatar_equipment`
What's currently equipped, per slot. This is the enforcement point for "only one item per slot."

| Column | Type | Constraints |
|---|---|---|
| avatar_id | UUID | FK → avatars, PK (part 1) |
| slot | ENUM('hair','hat','top','bottom','shoes','accessory') | PK (part 2) |
| item_id | UUID | FK → clothing_items, NULL (empty slot allowed) |
| equipped_at | TIMESTAMPTZ | NOT NULL |

Composite PK (avatar_id, slot) makes "one item per slot" structurally impossible to violate — no app-level check needed.

### 2.5 `clothing_categories`
Lookup; also maps a category to the slot it equips into.

| Column | Type | Constraints |
|---|---|---|
| category_id | SMALLINT | PK |
| category_name | VARCHAR(50) | UNIQUE, NOT NULL |
| slot | ENUM(...) | NOT NULL — which avatar slot this category fills |

### 2.6 `clothing_items`
The shop catalog.

| Column | Type | Constraints |
|---|---|---|
| item_id | UUID | PK |
| name | VARCHAR(100) | NOT NULL |
| description | TEXT | NULL |
| category_id | SMALLINT | FK → clothing_categories, NOT NULL |
| price | INTEGER | NOT NULL, CHECK (price >= 0) |
| image_url | TEXT | NOT NULL |
| availability_status | ENUM('available','unavailable','upcoming') | NOT NULL, DEFAULT 'available' |
| collection_id | UUID | FK → clothing_collections, NULL — **extensibility hook**, unused today |
| created_at | TIMESTAMPTZ | NOT NULL |

`collection_id` is nullable and unused in v1, but its presence means seasonal drops, celebrity collections, and campaigns (all listed in "Future Features") slot in later as *rows in a new table*, not a schema migration on `clothing_items` itself.

### 2.7 `user_wardrobe`
Ownership record — permanent, survives unequipping.

| Column | Type | Constraints |
|---|---|---|
| wardrobe_id | UUID | PK |
| user_id | UUID | FK → users, NOT NULL |
| item_id | UUID | FK → clothing_items, NOT NULL |
| purchased_at | TIMESTAMPTZ | NOT NULL |

UNIQUE (user_id, item_id) — structurally prevents duplicate purchases. "Equipped" state lives in `avatar_equipment`, not here — a single source of truth for equip status avoids two tables disagreeing about what's worn.

### 2.8 `products`
The physical Coca-Cola SKUs.

| Column | Type | Constraints |
|---|---|---|
| product_id | UUID | PK |
| name | VARCHAR(100) | NOT NULL |
| sku | VARCHAR(50) | UNIQUE, NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

### 2.9 `qr_codes`
One row per physical QR code printed.

| Column | Type | Constraints |
|---|---|---|
| qr_id | UUID | PK |
| code | VARCHAR(64) | UNIQUE, NOT NULL, indexed — the actual scanned value |
| product_id | UUID | FK → products, NOT NULL |
| coin_value | INTEGER | NOT NULL, CHECK (coin_value > 0) |
| status | ENUM('active','redeemed','expired') | NOT NULL, DEFAULT 'active' |
| redeemed_by_user_id | UUID | FK → users, NULL |
| redeemed_at | TIMESTAMPTZ | NULL |
| expires_at | TIMESTAMPTZ | NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

CHECK: `(status = 'redeemed') = (redeemed_by_user_id IS NOT NULL AND redeemed_at IS NOT NULL)` — status and redemption fields can't fall out of sync. "Redeemed once only" falls directly out of the status field plus a row-level UPDATE guard (`WHERE status = 'active'`) in the application transaction.

### 2.10 `coin_transaction_types` (lookup)
`qr_redemption`, `competition_reward`, `clothing_purchase`, `vote_cast`, `refund`, `admin_adjustment` — each flagged with a direction (credit/debit).

### 2.11 `coin_transactions`
The ledger. Source of truth for `coin_balance`.

| Column | Type | Constraints |
|---|---|---|
| transaction_id | UUID | PK |
| user_id | UUID | FK → users, NOT NULL, indexed |
| type_id | SMALLINT | FK → coin_transaction_types, NOT NULL |
| amount | INTEGER | NOT NULL, CHECK (amount <> 0) — signed: positive = credit, negative = debit |
| balance_after | INTEGER | NOT NULL — snapshot for audit/debugging |
| qr_id | UUID | FK → qr_codes, NULL |
| wardrobe_id | UUID | FK → user_wardrobe, NULL |
| vote_id | UUID | FK → votes, NULL |
| competition_id | UUID | FK → competitions, NULL |
| created_at | TIMESTAMPTZ | NOT NULL, indexed |

Reference columns are nullable FKs rather than a polymorphic `(reference_type, reference_id)` pair — real foreign keys give you referential integrity that a polymorphic pair can't (Postgres can't validate a "type-dependent" FK).

### 2.12 `follows`
Self-referencing many-to-many on `users`.

| Column | Type | Constraints |
|---|---|---|
| follower_id | UUID | FK → users, PK (part 1) |
| followee_id | UUID | FK → users, PK (part 2) |
| created_at | TIMESTAMPTZ | NOT NULL |

CHECK (follower_id <> followee_id). Composite PK doubles as the "no duplicate follow" constraint. Index on `followee_id` separately for fast "who follows me" lookups (the PK already covers "who do I follow" via leading column).

### 2.13 `competition_requests`
The pre-competition negotiation. Separated from `competitions` because it has a genuinely different lifecycle — a request is either accepted or it isn't, and it never has a prize pool, votes, or a timer.

| Column | Type | Constraints |
|---|---|---|
| request_id | UUID | PK |
| challenger_id | UUID | FK → users, NOT NULL |
| opponent_id | UUID | FK → users, NOT NULL |
| duration_minutes | SMALLINT | NOT NULL, CHECK (duration_minutes IN (30, 60, 360, 1440)) — 30m / 1h / 6h / 24h |
| status | ENUM('pending','accepted','declined','cancelled') | NOT NULL, DEFAULT 'pending' |
| created_at | TIMESTAMPTZ | NOT NULL |
| responded_at | TIMESTAMPTZ | NULL |

CHECK (challenger_id <> opponent_id). A `pending` status here is a native ENUM (not a lookup table) since this set of four outcomes is closed and unlikely to grow — unlike `competitions.status`, which stays a lookup table below.

**Duration is a `CHECK` constraint, not a lookup table**, unlike the other closed-but-growable sets in this schema — the allowed values are just numbers, not entities that ever need a name, description, or extra attributes. If duration presets start varying by campaign or region later, that's the point to promote it into a `competition_duration_options` table; not needed now.

**Multiple pending requests are allowed** — that's just the absence of a uniqueness constraint on `(challenger_id, opponent_id, status='pending')`; nothing needs to block it.

**When one request is accepted:** in the same transaction, (1) insert the row into `competitions` below, (2) set this request's status to `accepted`, and (3) bulk-update every other `pending` request involving either the challenger or the opponent to `cancelled`. That third step is a straightforward `UPDATE ... WHERE status='pending' AND (challenger_id IN (...) OR opponent_id IN (...))` — no trigger required, since it only touches `competition_requests`, not the users-appear-in-two-columns problem below.

### 2.14 `competition_status` (lookup)
`active`, `completed` — deliberately just two, now that `pending`/`declined`/`cancelled` live on the request instead.

### 2.15 `competitions`
Only created once a request is accepted — this table now represents *live and finished* competitions exclusively.

| Column | Type | Constraints |
|---|---|---|
| competition_id | UUID | PK |
| request_id | UUID | FK → competition_requests, UNIQUE, NOT NULL — provenance back to the accepted request |
| challenger_id | UUID | FK → users, NOT NULL |
| opponent_id | UUID | FK → users, NOT NULL |
| status_id | SMALLINT | FK → competition_status, NOT NULL |
| prize_pool | INTEGER | NOT NULL, DEFAULT 0 |
| total_votes | INTEGER | NOT NULL, DEFAULT 0 |
| winner_id | UUID | FK → users, NULL |
| duration_minutes | SMALLINT | NOT NULL — copied from the request at acceptance |
| start_time | TIMESTAMPTZ | NOT NULL |
| end_time | TIMESTAMPTZ | **GENERATED ALWAYS AS** (start_time + duration_minutes * interval '1 minute') **STORED** |
| created_at | TIMESTAMPTZ | NOT NULL |

`challenger_id`/`opponent_id`/`duration_minutes` are all duplicated here from the request rather than requiring a join every time a competition is queried — deliberate denormalization, justified because the request's terms never change after acceptance (immutable history), so there's no sync-drift risk. CHECK (challenger_id <> opponent_id). CHECK (winner_id IN (challenger_id, opponent_id) OR winner_id IS NULL).

`end_time` as a Postgres **generated column** means it's computed by the database itself from `start_time` and `duration_minutes` — no code path, buggy or otherwise, can set it to an inconsistent value. Nobody edits `end_time` directly; they can't.

**The "one active competition per user" rule still can't be expressed as a plain constraint** — a user can appear in either the `challenger_id` or `opponent_id` column across different rows, and Postgres can't index "this value doesn't appear as either column, in any row, with status=active" declaratively. This needs a `BEFORE INSERT` trigger on `competitions` (or an equivalent application-transaction check) that rejects the insert if either party already has a row with `status='active'`. Splitting out requests doesn't remove this problem — it just isolates it to exactly one table instead of tangling it with request/decline logic too. I'll flag this again at implementation time.

### 2.16 `votes`

| Column | Type | Constraints |
|---|---|---|
| vote_id | UUID | PK |
| competition_id | UUID | FK → competitions, NOT NULL |
| voter_id | UUID | FK → users, NOT NULL |
| voted_for_user_id | UUID | FK → users, NOT NULL |
| created_at | TIMESTAMPTZ | NOT NULL |

UNIQUE (competition_id, voter_id) — one vote per user per competition, and structurally makes votes immutable (no UPDATE path is exposed by the app; a vote is insert-only). CHECK (voter_id <> voted_for_user_id) — no self-votes. CHECK (voted_for_user_id IN referenced via app logic to be either the challenger or opponent of that competition — not expressible as a plain CHECK since it needs a cross-table lookup; enforce via trigger or application transaction).

Each vote insert should, in the same transaction: insert a debit row into `coin_transactions`, increment `competitions.prize_pool` and `total_votes`.

### 2.17 `notification_types` (lookup)
`new_follower`, `competition_request`, `competition_accepted`, `competition_won`, `competition_lost`, `qr_redeemed`, `clothing_purchased`, etc. — open-ended by design since this list grows fastest.

### 2.18 `notifications`

| Column | Type | Constraints |
|---|---|---|
| notification_id | UUID | PK |
| user_id | UUID | FK → users, NOT NULL, indexed — recipient |
| type_id | SMALLINT | FK → notification_types, NOT NULL |
| actor_user_id | UUID | FK → users, NULL — who triggered it (follower, opponent, etc.) |
| metadata | JSONB | NULL |
| is_read | BOOLEAN | NOT NULL, DEFAULT false |
| created_at | TIMESTAMPTZ | NOT NULL, indexed |

`metadata JSONB` is the one deliberate deviation from strict normalization: notification payloads vary per type (a competition notification needs a competition_id, a follow notification just needs actor_user_id) and new notification types will keep appearing per your roadmap. Rather than adding a new nullable FK column to this table for every future notification type, the type-specific reference IDs live in JSONB. `actor_user_id` stays a real FK because it's common to nearly every notification type and benefits from real referential integrity.

---

## 3. Relationships & Cardinality

| From | To | Cardinality | Notes |
|---|---|---|---|
| users | avatars | 1 : 1 | |
| users | auth_providers | 1 : N | |
| avatars | avatar_equipment | 1 : N (max 6) | one row per slot |
| clothing_items | avatar_equipment | 1 : N | |
| clothing_categories | clothing_items | 1 : N | |
| users | user_wardrobe | 1 : N | |
| clothing_items | user_wardrobe | 1 : N | |
| products | qr_codes | 1 : N | |
| users | qr_codes | 1 : N | as redeemer |
| users | coin_transactions | 1 : N | |
| users | follows | N : M (self) | via follower_id/followee_id |
| users | competition_requests | N : M (self) | via challenger_id/opponent_id |
| competition_requests | competitions | 1 : 1 | one competition per accepted request |
| users | competitions | N : M (self) | via challenger_id/opponent_id (denormalized from request) |
| competitions | votes | 1 : N | |
| users | votes | 1 : N | as voter |
| users | notifications | 1 : N | as recipient |

---

## 4. Indexing Summary

Beyond PK/UNIQUE indexes (which Postgres creates automatically):

- `users(username)`, `users(email)` — login/search
- `qr_codes(code)` — redemption lookup
- `coin_transactions(user_id, created_at DESC)` — user transaction history
- `follows(followee_id)` — "who follows me"
- `competition_requests(challenger_id)`, `competition_requests(opponent_id)` — "my pending requests"
- `competitions(challenger_id)`, `competitions(opponent_id)` — "my competitions"
- `competitions(status_id)` where status='active' — partial index, used constantly for the active-competition check
- `notifications(user_id, is_read, created_at DESC)` — unread feed, the most common notification query

---

## 5. Cascading Rules

| Relationship | On delete of parent |
|---|---|
| users → avatars | CASCADE (avatar is meaningless without the user) |
| users → auth_providers | CASCADE |
| avatars → avatar_equipment | CASCADE |
| users → user_wardrobe | CASCADE |
| users → follows | CASCADE (both directions) |
| clothing_items → user_wardrobe / avatar_equipment | RESTRICT (a purchased/equipped item shouldn't vanish if the catalog entry is deleted — use `availability_status='unavailable'` instead of deleting) |
| competitions → votes | CASCADE |
| users → coin_transactions | RESTRICT (never delete a ledger row — this is your financial audit trail; deactivate the user instead via `is_active`) |

In practice I'd expect this app to soft-delete users rather than hard-delete, given the ledger and competition history requirements — hard deletes and financial/audit tables don't mix well.

---

## 6. Business Rules: DB-Enforced vs. Application-Enforced

**Enforced structurally by the schema (no app code needed):**
- One item per avatar slot (composite PK)
- No duplicate clothing purchases (unique constraint)
- One vote per user per competition (unique constraint)
- No self-follow, no self-competition, no self-vote (check constraints)
- QR code uniqueness (unique constraint)
- Non-negative coin balance and prices (check constraints)

**Requires a trigger or transaction-scoped application logic:**
- One active competition per user (cross-row, cross-column check)
- Auto-cancelling other pending requests on acceptance
- Vote target must be a participant in that specific competition
- Coin balance cache staying in sync with the ledger

I'd recommend implementing the trigger-requiring rules as Postgres triggers rather than only in application code — that way they hold even if someone writes to the DB outside the app (admin tools, migrations, etc.).

---

## 7. Future Feature Extension Points (not built now)

| Future feature | How it slots in |
|---|---|
| Seasonal / celebrity collections | New `clothing_collections` table; `clothing_items.collection_id` already exists |
| `user_statistics` (cached wins/losses/followers/etc.) | Pure addition — a table or materialized view refreshed from `follows`/`competitions`/`competition_requests`. MVP computes these live; add this the moment profile-page read load justifies it |
| Leaderboards | Reads `user_statistics` once it exists (or computes live pre-MVP) — no new relationships needed |
| Daily challenges | New `challenges` + `challenge_completions` tables, reusing `coin_transactions` for rewards |
| Recycling rewards | New transaction type row in `coin_transaction_types` — zero schema change |
| Multi-country / multi-language | New `countries`/`locales` tables; add nullable `country_id` to `users`; item names/descriptions move to an `item_translations` table if full i18n is needed |
| Admin dashboard | Reads existing tables; possibly an `admin_users` + `audit_log` table |
| AI outfit recommendations | Reads `user_wardrobe` + `clothing_items`; no schema change |

None of these require restructuring anything above — that was the design constraint, and it's satisfied by keeping lookups as tables (not hard enums) and by the JSONB escape hatch on notifications.

---

## 8. Open Questions Before We Finalize

1. **UUID vs BIGSERIAL for PKs** — UUIDs avoid exposing sequential IDs (competition counts, user counts) publicly, at a small storage/index cost. I defaulted to UUID; say the word if you'd rather use BIGSERIAL.
2. ~~Prize pool timing — fixed duration vs admin-configurable~~ **Resolved:** fixed presets (30m/1h/6h/24h), chosen at request creation, `end_time` derived automatically — see section 2.13/2.15.
3. **QR expiry** — is `expires_at` set per-code at creation, or a global TTL policy?

Let me know what you'd change, and once this is locked I'll write the SQLAlchemy models.
