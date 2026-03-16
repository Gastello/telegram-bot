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
- Edit custom text: choose variant -> edit text -> preview -> post
- Reject and ensure cleanup happens (all variant messages deleted)
- Block and ensure game goes to blacklist
- Press old buttons after post/reject/block and ensure nothing breaks
- Manual post generation: /generate APPID -> verify post appears in moderation with all buttons

## Message cleanup (Enhanced - Chat restrictions)

### Personal chat only
- ✅ Bot responds only in MOD_CHAT_ID (personal chat)
- ✅ Buttons work only in MOD_CHAT_ID
- ✅ Photo uploads work only in MOD_CHAT_ID
- ✅ Text edits work only in MOD_CHAT_ID
- ✅ Check console for [BUTTON SKIP], [UPLOAD SKIP], [HELP SKIP] when using wrong chat

### Preview message deletion
- ✅ Preview message deleted when publish/reject/block in personal chat
- ✅ Preview kept during upload custom, old prompts deleted
- ✅ Check console for [CLEANUP START], [CLEANUP MSG], [CLEANUP RESULT]
- ✅ Check console for [CLEANUP SKIP] for channel/group messages

### File cleanup
- ✅ Generated images deleted after post/reject/block
- ✅ Check console for [FILE CLEANUP] Deleted messages
- ✅ generated/ folder should be clean after operations

### Error handling
- Attempt delete in channel -> should log [CLEANUP SKIP] with chat_id
- Check that [CLEANUP WARN] appears only for real errors, not for permissions

## File cleanup (New)
- Generated images should be deleted after post/reject/block
- Check `generated/` folder, should be empty or have minimal files
- Console should show [FILE CLEANUP] Deleted messages for appid

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
- Failed deletes are reported in console with [CLEANUP WARN] prefix

## Backfill mode
- `python checker.py --backfill-since 1773100800 --backfill-limit 80`
- Confirm no rate-limit storm
- Remove backfill mode after one-off run

## Normal mode after backfill
- `python checker.py`
- Confirm it uses price-change candidates, not all changed apps

------------------------------------------------------------------------

## Cleanup improvements validation (New)

### Preview message deletion
- ✅ Preview message now deleted when publish/reject/block
- ✅ During upload custom: preview kept, old prompts deleted
- ✅ Check console for [CLEANUP RESULT] with count

### Logging improvements
- Check console for: `[CLEANUP] moderation_id=123 message_id=456 kind=variant_1 deleted`
- Check console for: `[CLEANUP RESULT] moderation_id=123 deleted=5 failed=0`  
- Check console for: `[FILE CLEANUP] Deleted generated/game_appid_variant_1.png`

### Error handling
- Attempt delete on non-existent message -> should log [CLEANUP WARN], not crash
- Check that [CLEANUP WARN] messages have full error context

