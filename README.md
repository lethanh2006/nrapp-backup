# Backup database NRApp trên VPS và GitHub

Repository: https://github.com/lethanh2006/nrapp-backup

Hệ thống triển khai trên VPS `103.116.52.35`, user `deploy`, backend tại
`/opt/nrapp/backend`. Mã backup tại `/opt/nrapp/backup/current`; dữ liệu mã hóa
tại `/opt/nrapp-backups`. Repo chứa mã và tài liệu; dữ liệu backup được lưu
dưới dạng **GitHub Actions artifact mã hóa**, không commit vào Git.

## Thành phần được sao lưu

| Thành phần | File trong bản backup | Giới hạn |
|---|---|---|
| MongoDB Atlas, database của User service | `mongo.archive.gz` | Live dump từng collection, không phải snapshot toàn hệ thống |
| Redis | `redis.rdb` | Snapshot qua replication của `redis-cli --rdb` |
| RabbitMQ | `rabbitmq-definitions.json` | Exchanges, queues, bindings, users; không chứa message đang chờ |
| Backend | `backend-config.tar.gz` | `.env`, Compose và file trong `docker/`; chứa secret nên phải mã hóa |
| Payment PostgreSQL | `payment.dump` | Tự thêm khi container chạy; hiện chưa triển khai Payment |
| Metadata và checksum | `manifest.json`, `SHA256SUMS` | Ghi phiên bản mã, công cụ, phạm vi backup |

Backup không chứa file media Cloudinary, snapshot OS, dữ liệu Prometheus/Grafana
hay toàn bộ ổ VPS. Khôi phục database không tự khôi phục các tài nguyên đó.

## Lịch tự động và thời gian giữ

1. Systemd user timer trên VPS chạy lúc **02:30 giờ Việt Nam**, lệch ngẫu nhiên
   tối đa 5 phút. `Persistent=true` chạy bù khi timer bị gián đoạn. User `deploy`
   được bật linger để timer hoạt động khi không có phiên SSH.
2. GitHub Actions chạy lúc **03:15 giờ Việt Nam** để lấy bản mã hóa mới nhất.
   Nếu bản gần nhất đã quá 23 giờ, lệnh export tạo bản mới trước khi truyền.
   GitHub có thể chạy schedule trễ; xem timestamp thực tế trong Actions.
3. VPS giữ bản thuộc hệ thống này trong 14 ngày và luôn giữ ít nhất 7 bản.
   Chỉ dọn sau khi tạo thành công một bản mới. Backup cũ chưa có bản ngoài VPS
   vẫn có thể hết hạn: cần theo dõi workflow offsite nếu GitHub lỗi nhiều ngày.
4. GitHub artifact giữ **30 ngày**, hết hạn sẽ bị xóa. Đây không phải kho lưu
   trữ vĩnh viễn; tải bản quan trọng về máy hoặc bổ sung object storage.

Repo hiện public: GitHub có thể tắt schedule sau 60 ngày không có hoạt động repo.
Timer VPS vẫn chạy, nhưng cần kiểm tra và bật lại workflow offsite khi bị tắt.
Xem [quy định schedule của GitHub](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).

Mục tiêu ban đầu là RPO khoảng 24 giờ khi cả hai lịch chạy thành công.
RTO cần đo trong diễn tập phục hồi hệ thống, không suy ra từ việc dump thành công.

## CI/CD

Push vào `main` chạy kiểm tra Python, Bash, ShellCheck và systemd units. Chỉ khi
CI thành công mới triển khai đúng commit đó lên VPS. Pull request chỉ chạy CI.
Receiver giới hạn archive 1 MiB, kiểm tra đường dẫn, loại file và SHA; chuyển
symlink `current` sau khi kiểm tra. Các lần deploy và backup dùng lock để tránh
thay mã giữa lúc backup chạy. CD cập nhật cả service/timer và reload systemd.

GitHub secret `BACKUP_SSH_KEY` là khóa SSH riêng cho backup. Authorized key trên
VPS dùng `restrict` và forced command, chỉ nhận `export` hoặc `deploy <SHA>`;
không cấp shell SSH hay port forwarding bằng khóa này. Người có quyền sửa workflow
và sử dụng secret vẫn có quyền cập nhật mã backup; cần giữ an toàn tài khoản GitHub.

Host key VPS được pin trong `deploy/known_hosts`. Nếu VPS được dựng lại, xác nhận
fingerprint qua kênh quản trị trước khi thay file; không tắt host key checking.

## Khóa mã hóa

Age dùng khóa công khai trên VPS để mã hóa. Khóa bí mật giải mã chỉ được tạo trên
máy cá nhân tại:

```text
/home/thanhle/.config/nrapp-backup/recovery.key
```

File này có quyền `600`, không đưa vào repository, GitHub secret hay VPS.
**Hãy giữ thêm một bản khóa ở nơi an toàn khác. Mất khóa sẽ không giải mã được backup.**
Đổi recipient chỉ ảnh hưởng bản mới; vẫn cần khóa cũ để mở bản cũ.

Age binary tại `/opt/nrapp/backup/bin/age`; MongoDB tools chạy bằng Docker image
pin theo digest trong `/opt/nrapp/backup/config.json`. URI MongoDB được đọc từ
container User và đưa vào file config tạm quyền `600`, không truyền URI qua argv.
Dữ liệu tạm được dọn sau khi kết thúc; payload chỉ được công bố sau khi mã hóa xong.

## Chạy và kiểm tra

Kết nối bằng khóa quản trị hiện có:

```bash
ssh -o IdentitiesOnly=yes -i /home/thanhle/.ssh/nrapp_vps deploy@103.116.52.35
systemctl --user list-timers nrapp-backup.timer --no-pager
systemctl --user start nrapp-backup.service
systemctl --user status nrapp-backup.service --no-pager
journalctl --user -u nrapp-backup.service -n 30 --no-pager
ls -lh /opt/nrapp-backups/nrapp-*.tar.age
```

Trên máy cá nhân, đẩy bản ngoài VPS ngay:

```bash
gh workflow run backup.yml --repo lethanh2006/nrapp-backup
gh run list --repo lethanh2006/nrapp-backup --workflow backup.yml --limit 5
```

Workflow lỗi không tạo artifact hợp lệ. Backup chỉ được đánh dấu thành công sau
khi dump/copy/mã hóa thành công; export kiểm tra SHA-256 trước khi truyền.
Lỗi công cụ chỉ in loại lỗi để tránh vô tình đưa credential vào log. Nếu lỗi,
kiểm tra container, cấu hình và kết nối Atlas trên VPS; không in `.env` ra Actions.

## Tải và giải mã để phục hồi

Chạy tại thư mục cá nhân quyền `700`, ngoài repository. Thay `RUN_ID` và tên file
bằng giá trị thực tế trong Actions; age trên máy này nằm tại
`/mnt/data/nrapp-backup-tools/age/age`.

```bash
umask 077
mkdir -p /home/thanhle/nrapp-recovery
cd /home/thanhle/nrapp-recovery
gh run download RUN_ID --repo lethanh2006/nrapp-backup --dir encrypted
cd encrypted/nrapp-encrypted-RUN_ID
sha256sum -c ./*.sha256
mkdir -m 700 ../../payload
/mnt/data/nrapp-backup-tools/age/age --decrypt \
  -i /home/thanhle/.config/nrapp-backup/recovery.key \
  -o ../../payload/payload.tar nrapp-TIMESTAMP.tar.age
tar -xf ../../payload/payload.tar -C ../../payload
cd ../../payload
sha256sum -c SHA256SUMS
cat manifest.json
```

Đầu tiên diễn tập trên MongoDB riêng, không restore thẳng production:

```bash
docker run -d --name nrapp-restore-drill --memory 512m \
  mongo@sha256:6c108efd306c66a7eb58e0b4042c2fcffb4a4980dac111039c59fd714db6a69b
docker cp mongo.archive.gz nrapp-restore-drill:/tmp/mongo.archive.gz
docker exec nrapp-restore-drill mongorestore \
  --archive=/tmp/mongo.archive.gz --gzip --stopOnError
docker exec nrapp-restore-drill mongosh --quiet --eval \
  'const d=db.getSiblingDB("nrapp"); printjson(d.getCollectionNames().map(n=>({collection:n,count:d[n].countDocuments({})})))'
docker rm -fv nrapp-restore-drill
```

Database thực tế xem `manifest.json`; thay `nrapp` nếu khác. Kiểm tra số collection,
document, index và nghiệp vụ trên môi trường thử trước khi quyết định phục hồi thật.
Với MongoDB Atlas, tạo file `--config` riêng cho môi trường đích để tránh lộ URI;
không dùng `--drop` trên production nếu chưa xác định việc xóa dữ liệu hiện có.

Để phục hồi Redis, thử `redis.rdb` trong volume/container riêng trước; ngừng Redis
đích trong cửa sổ bảo trì, khôi phục theo chính sách RDB/AOF đã chọn. AOF cũ có thể
được ưu tiên hơn RDB nên không chỉ chép RDB rồi restart và coi là đã phục hồi.
RabbitMQ definitions import chỉ khôi phục cấu hình, không hồi phục message đã mất.
PostgreSQL dùng `pg_restore --exit-on-error` vào database thử trước nếu manifest
có `postgres: true`. Khôi phục `.env` cần quyền chặt chẽ và đối chiếu credential.

## Kết quả thực thi ngày 16/09/2026

- [CI/CD thành công](https://github.com/lethanh2006/nrapp-backup/actions/runs/35077039220):
  kiểm tra code, deploy và cập nhật timer trên VPS.
- Systemd service đã chạy thật, kết thúc `status=0/SUCCESS`; lần này tạo file
  `nrapp-20260916T090241Z.tar.age`.
- [Backup ngoài VPS thành công](https://github.com/lethanh2006/nrapp-backup/actions/runs/35077097126):
  artifact `nrapp-encrypted-35077097126`, khoảng 31 KB, hạn giữ đến 16/10/2026.
- Đã tải bản từ GitHub, kiểm tra checksum bên ngoài, giải mã bằng khóa trên máy
  cá nhân và kiểm tra mọi file theo `SHA256SUMS` bên trong.
- Đã restore MongoDB vào container riêng trên VPS, không mở port, dùng network
  riêng bị ngắt kết nối ngoài: **19 collections, 4 documents, 53 indexes**;
  `mongorestore` báo **0 documents failed**. Đây là dữ liệu tại thời điểm kiểm tra,
  không phải yêu cầu số lượng cố định của lần backup sau.
- Redis snapshot đã qua `redis-check-rdb`; RabbitMQ definitions đọc được dưới
  dạng JSON. Chưa diễn tập phục hồi Redis/RabbitMQ/Payment hay toàn bộ ứng dụng.
- API `/health` vẫn trả HTTP 200 sau diễn tập. Container thử và dữ liệu thử được
  dọn; không restore vào Atlas production.

## Giới hạn nhất quán MongoDB

Live dump không dừng ứng dụng; các collection có thể phản ánh những thời điểm
khác nhau khi có thao tác ghi. Với đơn hàng/thanh toán hoặc trước migration quan
trọng, cần cửa sổ bảo trì: dừng mọi nguồn ghi, chờ xử lý đang chạy hoàn tất, tạo
backup, bật lại ứng dụng và kiểm tra sức khỏe. Không tự dừng production trong lịch
hằng ngày. Atlas Free không dùng `mongodump --oplog`; nếu cần snapshot/PITR thì
chuyển sang cơ chế Atlas/tier hỗ trợ và diễn tập restore.

Nguồn tham khảo:

- [MongoDB mongodump](https://www.mongodb.com/docs/database-tools/mongodump/)
- [GitHub workflow artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data)
- [GitHub upload-artifact](https://github.com/actions/upload-artifact)
- [Age](https://github.com/FiloSottile/age)
