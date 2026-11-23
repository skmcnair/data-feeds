# Feed settings (feeds/<feed>/feed.yml)

One short file per feed. The engine, the tracker and Claude's daily review
all read it, so every rule for a feed lives here. `feeds/_template/feed.yml`
is a worked example.

## About the feed

| Setting | Meaning |
| --- | --- |
| `title` | Name shown on the tracker and in messages. |
| `item` | What one row is called: deal, move, opening. |
| `scope` | What's in and what's out, in plain words. Claude follows it. |
| `instructions` | Anything else Claude should know when researching. |
| `trial` | `true` for the first week: the morning message lists every row. |
| `paused` | `true` stops collection, reviews and health alerts. |

## Fields

`fields` is the list of columns, in order. Each has a `name` (snake_case,
never renamed once a board uses it), a `type`, a `label`, and optionally
`required`, `help` (guidance for Claude), `pattern` and `pattern_hint`
(a rule the value must match, and how to explain it), `scored` (counts
toward confidence; required fields count by default) and `tracker: false`
(hide on the tracker page).

| Type | Stored as | Published as |
| --- | --- | --- |
| `text` | plain text | as is |
| `date` | YYYY-MM-DD | `name` "Nov 19, 2026" and `name_iso` "2026-11-19" |
| `datetext` | a date, or text like "Jan 2027" | `name` and `name_iso` (blank if not a full date) |
| `choice` | one of `choices` | as is |
| `int` | whole number | "131,224" |
| `money` | $ millions | `name` "$1.85B" or "$345M"; `name_m` "1850.0" |
| `percent` | 0-100 | "97%" |
| `title` | job title in the house style | "VP, Marketing", "SVP, Chief Lending Officer", "President & CEO" |
| `org` | organization name | `name`, `name_short`, `name_logo_url`, `name_logo_style` from the shared organization list (each record remembers which entry it means; a name two entries share needs `org_slugs`, see REVIEW.md) |
| `url` | web address | as is |
| `state` | two-letter state or National | as is |
| `yesno` | yes or no | as is |

Every published row also has `id`, `sort_date`, `source_url`, `sources`
("Label: url | ..."), `updates` (dated notes, newest first), `added`,
`updated`, `last_checked`, `open` (yes/no), and with confidence on,
`confidence` and `confidence_notes`.

## Identity and change over time

| Setting | Meaning |
| --- | --- |
| `date_field` | The item's main date. The feed is sorted newest first by it. |
| `key` | Fields that make two items the same (for example `[name, organization]`). Duplicates fill blanks in the existing row instead of adding one. Key fields can't be changed by an update. |
| `open_when` | Which items are still open, for example `{field: status, not_in: [Completed, Called off]}`. Open items are re-checked by Claude, oldest first. |
| `close_after_days` | Items with no news for this many days are closed. |
| `publish.max_age_days` | Closed items older than this drop out of the published feed (they stay in the records). |

## Derived fields

Worked out from other published fields on every build.

```yaml
derived:
  - name: region
    map_from: state
    groups: {New England: [CT, ME, MA, NH, RI, VT]}
    default: Elsewhere
  - name: headline
    cases:
      - when: {status: Completed}
        template: "{acquirer_short} completes merger with {partner_short}"
      - template: "{acquirer_short} and {partner_short} to merge"
```

## Sources and collection

```yaml
sources:
  - {name: CU Today, type: rss, url: https://...}           # any RSS or Atom feed, including RSS.app
  - {name: News search, type: google_news, query: '"credit union" merger'}
collect:
  include_any: [merger, merge]     # keep only articles mentioning one of these
  exclude_any: [obituary]
  max_age_days: 14
  max_pending: 300
```

Collection runs on GitHub every three hours. Sites behind a login
(LinkedIn) can't be collected directly; an RSS.app feed of a public page can.

## Review and health

| Setting | Meaning |
| --- | --- |
| `review.time` | When the daily Claude review runs, Eastern (match the scheduled task). |
| `review.lookback_days`, `review.monday_lookback_days` | How far back Claude's own searches go. |
| `review.recheck_max` | Most open items re-checked per run. |
| `review.searches` | Searches Claude runs every day. |
| `review.grace_minutes` | How long after `review.time` the health check waits (default 120). |
| `health.collect_max_hours` | Alert if collection is older than this (default 8). |
| `message.new_line`, `message.update_line`, `message.notes` | How the morning message reads. Templates use published field names. |

## Confidence

`confidence: {enabled: true}` scores each row from how its facts were
sourced: primary and regulator sources 1.0, trade press 0.9, aggregators
0.75, social media 0.7, blanks 0; Sean's corrections count as confirmed.
The score is capped at 99%. Change the weights with
`confidence: {weights: {trade_press: 0.85}}`. (This setting used to be called
`accuracy`; the old name is still accepted.)
