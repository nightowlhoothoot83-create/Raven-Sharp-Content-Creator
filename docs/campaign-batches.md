# Offline campaign batches

`python tools/campaign_batch.py --destination https://YOUR-LIVE-TOOL/path --out /workspace/outputs/craft-fair-campaign`

This supplements the existing production renderer and video composer. It packages original craft-fair cost education into four captioned 720x1280 PNGs, an optional 12-second silent MP4 if ffmpeg exists, five platform-specific captions, tracked destinations and payloads compatible with `/api/v2/render/carousel`. No provider inference or remote API calls occur. Pillow is already part of backend requirements. Original text/design uses local DejaVu fonts, no stock media or music.

The manifest is prepared, never scheduled/published. Validate destination and audience fit, upload assets to an authorised public media host, supply a Pinterest public board, and use existing Ad Manager approval or connected Metricool MCP for publication. Instagram/TikTok/YouTube captions use profile-link instructions: the profile link must actually be configured before publishing. YouTube requires its title and adult/general audience setting. No native TikTok Shop tagging, Instagram personal profiles, YouTube Community posts or Facebook Groups are claimed.

For approved affiliate campaigns, keep the exact network-issued referral URL unchanged and place clear disclosure at the beginning. The library rejects unapproved affiliate calls. This is an operator validation flag, not proof of network approval. Never replace affiliate tracking with a made-up referral ID. Null metrics mean unavailable, not zero. Keep pending, confirmed and received commission distinct in the network ledger.

Batch production and publishing belong on a suitable production runtime; existing GitHub CI only checks code. No commercial cron is added. Regenerate after destination changes and reconcile publication receipts rather than resubmitting blindly.
