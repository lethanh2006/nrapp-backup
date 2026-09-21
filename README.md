# Backup NRApp

Repository này chứa worker backup mã hóa, SSH receiver giới hạn quyền, các
systemd unit và workflow GitHub Actions cho VPS của NRApp. Backup được tạo trên
VPS, mã hóa bằng `age`, kiểm tra bằng SHA-256 rồi sao chép lên GitHub dưới dạng
artifact mã hóa và file trong repository.

Backup worker không chạy như một Docker service. Systemd user timer trên VPS gọi
worker và làm việc với các container NRApp đang chạy.

## Thành phần backup

Một archive mã hóa thành công có thể chứa:

| Thành phần | Nguồn và định dạng |
| --- | --- |
| MongoDB | Dump live theo từng collection của database được cấu hình trong User service, `mongo.archive.gz` |
| Redis | `redis.rdb` tạo bằng `redis-cli --rdb` và kiểm tra bằng `redis-check-rdb` |
| RabbitMQ | `rabbitmq-definitions.json` gồm exchange, queue, binding và user; không chứa message đang chờ |
| PostgreSQL Payment | `payment.dump` tùy chọn, chỉ tạo khi container `payment-postgres` đang chạy |
| Cấu hình backend | `backend-config.tar.gz` được mã hóa, gồm file env backend, file Compose và file triển khai trong `docker/` |
| Metadata | `manifest.json` và `SHA256SUMS`, gồm revision backup đang deploy và checksum các thành phần |

Archive không chứa media Cloudinary, snapshot hệ điều hành, dữ liệu
Prometheus/Grafana hoặc image toàn bộ ổ đĩa VPS. Vì vậy khôi phục database không
tự khôi phục các tài nguyên này.

## Luồng backup

```text
Systemd timer trên VPS (19:30 UTC, khoảng 02:30 giờ Việt Nam)
  -> scripts/backup.py run
  -> export MongoDB, Redis, RabbitMQ và PostgreSQL nếu có
  -> tạo manifest + checksum SHA-256
  -> mã hóa bằng age
  -> /opt/nrapp-backups/nrapp-<timestamp>.tar.age

GitHub Actions (20:15 UTC, khoảng 03:15 giờ Việt Nam)
  -> SSH command giới hạn quyền: export
  -> lấy archive và checksum mới nhất
  -> kiểm tra SHA-256
  -> upload artifact giữ 30 ngày
  -> commit archive mã hóa, checksum và backups/LATEST
```

Lệnh export sẽ tạo backup mới nếu archive mới nhất không tồn tại hoặc đã cũ hơn
23 giờ. Cơ chế này bảo vệ job off-site khi VPS bỏ lỡ timer nhưng không thay thế
timer. VPS giữ archive tối đa 14 ngày và luôn giữ ít nhất bảy bản khi việc dọn
rác thành công. Mục tiêu RPO là khoảng 24 giờ khi cả hai lịch chạy hoàn tất; RTO
phải được đo bằng một lần diễn tập khôi phục thực tế.

## Mã hóa và kiểm soát truy cập

`age` mã hóa toàn bộ payload trước khi payload rời khỏi worker backup. Khóa giải
mã được tạo và giữ trên máy recovery của quản trị viên; khóa không nằm trong
repository, VPS hoặc GitHub secrets. Hãy giữ thêm một bản khóa ở nơi offline an
toàn.

GitHub secret `BACKUP_SSH_KEY` sử dụng host key được pin trong
`deploy/known_hosts` và forced command trên VPS. Key chỉ chấp nhận `export` hoặc
`deploy <40-character-commit-sha>`, không cấp shell tương tác hay port
forwarding. Path, kích thước archive, checksum và revision deploy đều được kiểm
tra trước khi chuyển symlink active.

## Các file trong repository

- `scripts/backup.py`: tạo (`run`) và export (`export`) backup mã hóa.
- `scripts/receiver.sh`: forced-command receiver cho export và deploy đúng
  revision.
- `systemd/nrapp-backup.service`: backup service dạng one-shot.
- `systemd/nrapp-backup.timer`: timer chạy hằng ngày, lệch ngẫu nhiên năm phút
  và có `Persistent=true` để chạy bù.
- `.github/workflows/backup.yml`: export off-site theo lịch, kiểm tra, upload
  artifact và commit archive mã hóa.
- `.github/workflows/ci-cd.yml`: kiểm tra Python, shell, systemd rồi deploy đúng
  revision của backup code trên `main`.
- `VPS_BACKUP_OPERATIONS.md`: hướng dẫn kiểm tra vận hành, chạy thủ công và khôi
  phục.

## CI/CD

Pull request và push thay đổi backup code sẽ chạy kiểm tra bytecode Python,
`bash -n`, ShellCheck và kiểm tra systemd unit. Push chỉ thay đổi `backups/**`
sẽ bị bỏ qua bởi deploy workflow. Push thành công vào `main` chỉ deploy các thư
mục `scripts`, `systemd` và file `REVISION` được tạo trong pipeline; receiver
chuyển symlink active sau khi kiểm tra archive và revision.

Cấu hình GitHub cần có:

- `BACKUP_SSH_KEY`: private key giới hạn quyền được hai workflow sử dụng.
- `deploy/known_hosts`: host key đã pin của VPS.

Chạy các kiểm tra tương tự ở local:

```bash
python3 -m py_compile scripts/backup.py
bash -n scripts/receiver.sh
shellcheck scripts/receiver.sh
systemd-analyze --user verify systemd/nrapp-backup.service systemd/nrapp-backup.timer
```
