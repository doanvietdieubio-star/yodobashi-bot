# Yodobashi comment monitor (gamenv.net -> Discord)

Theo dõi bình luận tại https://gamenv.net/tc/yodobashi/ và gửi Discord Embed.

## Cài đặt
1. Tạo repo **Public** trên GitHub, upload toàn bộ file (giữ nguyên `.github/workflows/main.yml`).
2. Settings -> Secrets and variables -> Actions:
   - `DISCORD_WEBHOOK_URL` (bắt buộc)
   - `SITE_COOKIE` (tùy chọn, sau 18/11): toàn bộ header Cookie khi đã đăng nhập, dạng `a=1; b=2`
3. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
4. Actions -> Run workflow (tick debug) để chạy thử. Lần đầu chỉ ghi nhớ bình luận hiện có.

## Tinh chỉnh
- Nhận diện chi nhánh: xem `debug/comments_raw.json` (trường `badges`), sửa `BRANCH_RULES` hoặc dùng `BRANCH_MAP_JSON`.
- Bình luận ghim bị sót: thêm ID vào `IGNORE_IDS`.
