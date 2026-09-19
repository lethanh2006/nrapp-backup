# Vận hành và kiểm tra backup trên VPS

Tài liệu này giải thích file .yml, luồng backup NRApp và các lệnh kiểm tra thực tế
trên VPS. Các lệnh bên dưới chạy bằng user deploy, không cần sudo cho
systemctl --user.

## 1. .yml là gì?

.yml hoặc .yaml là file cấu hình dạng văn bản. Nó không tự chạy như script Bash
hay Python; một chương trình sẽ đọc file này để biết phải làm gì.

Trong repo này:

- .github/workflows/backup.yml: cấu hình GitHub Actions lấy backup từ VPS, kiểm
  tra checksum, lưu Artifact và commit archive mã hóa vào Git.
- .github/workflows/ci-cd.yml: kiểm tra mã và triển khai script/systemd lên VPS khi
  có thay đổi mã. Commit chỉ thay đổi backups/** được bỏ qua deploy.
- systemd/nrapp-backup.timer: lịch chạy backup trên VPS.
- systemd/nrapp-backup.service: lệnh thực sự gọi backup.py run.

Cron trong GitHub Actions dùng UTC. Ví dụ:

~~~yaml
- cron: '15 20 * * *' # 03:15 giờ Việt Nam
~~~

GitHub có thể chạy schedule trễ. Vì vậy timestamp thực tế trong Actions mới là
mốc xác nhận chính xác.

## 2. Luồng hoạt động

~~~text
systemd timer trên VPS
        |
        v
nrapp-backup.service
        |
        v
backup.py run
        |
        +--> Docker User service --> mongodump MongoDB
        +--> Docker Redis      --> redis.rdb
        +--> Docker RabbitMQ   --> definitions.json
        +--> đóng gói + SHA256 + age mã hóa
        |
        v
/opt/nrapp-backups/nrapp-<timestamp>.tar.age

GitHub Actions theo lịch
        |
        +--> SSH forced command: export
        +--> lấy archive mới nhất từ VPS
        +--> kiểm tra SHA256
        +--> upload Artifact 30 ngày
        +--> commit archive mã hóa vào backups/ trên Git
~~~

Có hai trường hợp GitHub gọi export:

1. Nếu bản gần nhất chưa quá 23 giờ, workflow chỉ lấy bản đó.
2. Nếu bản gần nhất quá 23 giờ hoặc không tồn tại, export tự gọi lại quy trình
   tạo backup trước khi truyền. Đây là cơ chế dự phòng, không thay thế timer VPS.

## 3. Các vị trí quan trọng trên VPS

| Vị trí | Ý nghĩa |
|---|---|
| /opt/nrapp/backup/current | Mã backup đang được deploy |
| /opt/nrapp/backup/current/scripts/backup.py | Tạo và export backup |
| /opt/nrapp/backup/current/REVISION | Commit mã backup đang chạy |
| /opt/nrapp-backups | Archive .tar.age, checksum và LATEST |
| /home/deploy/.config/systemd/user/nrapp-backup.timer | Timer systemd user |
| /home/deploy/.config/systemd/user/nrapp-backup.service | Service systemd user |
| /opt/nrapp/backend | Compose và các service NRApp |

docker ps không có container tên backup là bình thường. Backup chạy bằng systemd
trên host; MongoDB tool là container tạm thời, có --rm, chỉ tồn tại trong lúc
dump rồi tự xóa.

## 4. Kết nối và kiểm tra nhanh

Chạy trên máy cá nhân:

~~~bash
ssh -o IdentitiesOnly=yes \
  -i /home/thanhle/.ssh/nrapp_vps \
  deploy@103.116.52.35
~~~

Sau khi vào VPS:

~~~bash
whoami
date -Is
~~~

Kết quả cần thấy user là deploy. Kiểm tra đồng hồ giúp đối chiếu timestamp giữa
systemd, tên file backup và GitHub Actions.

## 5. Kiểm tra lịch systemd

~~~bash
systemctl --user list-timers nrapp-backup.timer --all --no-pager
systemctl --user status nrapp-backup.timer --no-pager
systemctl --user show nrapp-backup.timer \
  -p ActiveState -p UnitFileState -p NextElapseUSecRealtime -p LastTriggerUSec
~~~

Đọc kết quả:

- Active: active (waiting): timer đang chờ lần chạy kế tiếp.
- enabled: timer được bật.
- NEXT: thời điểm chạy tiếp theo.
- LAST: lần timer vừa kích hoạt service.
- RandomizedDelaySec=300: lệch ngẫu nhiên tối đa 5 phút.

Timer hiện được cấu hình khoảng 02:30 giờ Việt Nam, không phải 05:00:

~~~text
OnCalendar=*-*-* 19:30:00 UTC
~~~

## 6. Kiểm tra service và log

~~~bash
systemctl --user status nrapp-backup.service --no-pager
systemctl --user show nrapp-backup.service \
  -p ActiveState -p Result -p ExecMainStatus -p ExecMainStartTimestamp
journalctl --user -u nrapp-backup.service -n 80 --no-pager
~~~

Các trạng thái thường gặp:

| Kết quả | Ý nghĩa |
|---|---|
| status=0/SUCCESS hoặc Result=success | Backup hoàn tất |
| Active: inactive (dead) sau SUCCESS | Bình thường vì service là Type=oneshot |
| Active: failed | Lần backup gần nhất lỗi, cần đọc journal |
| BACKUP_STAGE: MongoDB rồi BACKUP_FAILED | Lỗi ở dump MongoDB/tool/kết nối |
| BACKUP_STAGE: Encryption rồi BACKUP_OK | Đã mã hóa và ghi archive thành công |

failed không có nghĩa là process đang treo. Với service oneshot, process sẽ kết
thúc sau khi chạy xong; cần nhìn Result và dòng BACKUP_OK/BACKUP_FAILED.

Xem log theo khoảng thời gian:

~~~bash
journalctl --user -u nrapp-backup.service \
  --since 'today 02:20' --until 'today 03:00' --no-pager
~~~

## 7. Kiểm tra file backup và checksum

~~~bash
ls -lahtr /opt/nrapp-backups
cat /opt/nrapp-backups/LATEST
~~~

Kiểm tra bản mới nhất:

~~~bash
cd /opt/nrapp-backups
latest=$(cat LATEST)
test -f "$latest"
test -f "$latest.sha256"
sha256sum -c "$latest.sha256"
stat "$latest" "$latest.sha256" LATEST
~~~

Kết quả hợp lệ phải có OK từ sha256sum. File .tar.age là ciphertext đã mã hóa;
không dùng tar -xf trực tiếp để đọc nội dung bên trong.

Kiểm tra tuổi các bản backup:

~~~bash
find /opt/nrapp-backups -maxdepth 1 -type f \
  -name 'nrapp-*.tar.age' -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' | sort
~~~

## 8. Kiểm tra container và tài nguyên

~~~bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
/home/deploy/bin/dc ps
df -h / /opt /var/lib/docker
df -ih / /opt /var/lib/docker
~~~

Các service cần healthy cho backup hiện tại thường gồm user, redis và rabbitmq.
Kiểm tra không lộ environment:

~~~bash
docker inspect --format \
  'name={{.Name}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' \
  nrapp-backend-user-1 nrapp-backend-redis-1 nrapp-backend-rabbitmq-1
~~~

Không dùng docker inspect không có --format, cat .env hoặc printenv trong báo cáo
vì có thể làm lộ credential.

## 9. Chạy backup thủ công

Lệnh sau thực sự tạo backup mới, chỉ chạy khi cần kiểm tra vận hành:

~~~bash
systemctl --user start nrapp-backup.service
systemctl --user status nrapp-backup.service --no-pager
journalctl --user -u nrapp-backup.service -n 80 --no-pager
ls -lhtr /opt/nrapp-backups/nrapp-*.tar.age
~~~

Không cần chạy docker run backup bằng tay. Service đã gọi đúng script, lock,
quyền file, MongoDB tool, Redis, RabbitMQ và age encryption.

## 10. Kiểm tra mã đang deploy

~~~bash
readlink -f /opt/nrapp/backup/current
cat /opt/nrapp/backup/current/REVISION
sha256sum /opt/nrapp/backup/current/scripts/backup.py
~~~

Nếu current không trỏ tới release mới hoặc REVISION không đúng commit trên GitHub,
CD chưa deploy xong hoặc deployment bị bỏ qua do stale revision.

## 11. Kiểm tra GitHub Actions và Git

Các lệnh này nên chạy trên máy cá nhân có GitHub CLI, không nhất thiết trên VPS:

~~~bash
gh run list --repo lethanh2006/nrapp-backup \
  --workflow backup.yml --limit 5
gh run view RUN_ID --repo lethanh2006/nrapp-backup
git clone https://github.com/lethanh2006/nrapp-backup.git
cd nrapp-backup
cd backups
cat LATEST
sha256sum -c "$(cat LATEST).sha256"
~~~

Một run thành công cần có các bước:

~~~text
Fetch encrypted backup and verify       ✓
Store encrypted archive for 30 days     ✓
Commit encrypted backup to repository   ✓
~~~

Nếu Artifact thành công nhưng không có commit Git, kiểm tra quyền contents: write,
bước git commit, bước git push và branch main.

## 12. Bảng xử lý sự cố nhanh

### Timer không chạy

~~~bash
systemctl --user is-enabled nrapp-backup.timer
systemctl --user is-active nrapp-backup.timer
loginctl show-user deploy -p Linger
~~~

### Service lỗi ở MongoDB

~~~bash
journalctl --user -u nrapp-backup.service -n 100 --no-pager
/home/deploy/bin/dc ps user
docker inspect --format '{{.State.Health.Status}}' nrapp-backend-user-1
df -h /var/lib/docker /opt/nrapp-backups
~~~

Không in MongoDB URI. Nếu container healthy nhưng mongodump vẫn lỗi, kiểm tra kết
nối Atlas, DNS, firewall và MongoDB tool image; lần chạy GitHub sau đó có thể tạo
backup dự phòng nếu bản gần nhất đã quá 23 giờ.

### Không thấy backup trong docker ps

Đây là bình thường. Kiểm tra service và thư mục host:

~~~bash
systemctl --user status nrapp-backup.service --no-pager
ls -lah /opt/nrapp-backups
~~~

### Có file trên VPS nhưng chưa có trên Git

~~~bash
cd /opt/nrapp-backups
cat LATEST
sha256sum -c "$(cat LATEST).sha256"
~~~

Sau đó xem run backup.yml trên GitHub. Workflow lấy bản qua SSH export, không đọc
trực tiếp filesystem VPS bằng Git.

## 13. Checklist kết luận một bản backup thành công

- Timer là active (waiting) và có NEXT hợp lệ.
- Journal có BACKUP_OK: ...
- /opt/nrapp-backups/LATEST trỏ tới file tồn tại.
- sha256sum -c trả OK.
- Các container user, Redis và RabbitMQ đang healthy.
- GitHub Actions có run success.
- GitHub có Artifact và commit mới trong backups/.

Không đưa recovery.key, .env, URI MongoDB hoặc file plaintext vào Git.
