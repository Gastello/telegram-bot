# QA checklist for Steam Telegram bot

## Quick smoke test
1. `python checker.py --test-csv test_games.csv`
2. Verify logs appear in console and `logs/checker.log`
3. Verify at least one game goes to moderation

## Moderation flows
- Multi-variant post: choose variant -> preview -> post
- Single-variant post: reject/block/custom image buttons are visible
- Upload custom image as photo
- Upload custom image as file document
- Reject and ensure cleanup happens
- Block and ensure game goes to blacklist
- Press old buttons after post/reject/block and ensure nothing breaks

## Content edge cases
- Game with `page_bg_raw` missing but `library_hero` exists
- Game with no price data
- Game with no appdetails
- Game with no reviews data
- Discount below threshold
- Reviews below threshold
- Rating below threshold
- Date formats:
  - `Діє до ...`
  - `Закінчується ...`
  - `Закінчиться через ...`

## Telegram resilience
- Flood control retry works
- Timeout retry works
- Messages arrive in correct order
- Logs remain after cleanup, technical messages are deleted

## Backfill mode
- `python checker.py --backfill-since 1773100800 --backfill-limit 80`
- Confirm no rate-limit storm
- Remove backfill mode after one-off run

## Normal mode after backfill
- `python checker.py`
- Confirm it uses price-change candidates, not all changed apps
