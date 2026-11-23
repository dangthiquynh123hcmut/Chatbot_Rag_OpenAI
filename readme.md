# Hierarchy-Aware RAG cho tài liệu tiêu chuẩn

Hệ thống hỏi đáp trên tài liệu **PDF / DOCX** dài, phân tầng rõ ràng (Phần → Chương → Mục → Tiểu mục → Điều → Khoản → Điểm, hoặc kiểu giáo trình I. → 1. → 1.1 → a)).
Mục tiêu: từ câu hỏi, trả về **chính xác nguyên văn một tiêu mục cụ thể** trong tài liệu, kèm đường dẫn vị trí.

- Backend: **FastAPI**
- Vector DB: **PostgreSQL + pgvector** (kèm full-text search để tìm kiếm hybrid)
- Embedding / LLM / Rerank: chọn bằng `LLM_PROVIDER`
  - `bedrock` – Amazon Bedrock qua IAM credentials local: Claude Haiku 4.5, Titan Text Embeddings V2 (1024 chiều), Cohere Rerank 3.5
  - `openai` – `gpt-4o-mini`, `text-embedding-3-large` (rút gọn 1536 chiều)
- Frontend: **React + TypeScript** (Vite, strict mode)

## Luồng xử lý

```
PDF/DOCX ──► Markdown ──► Chuẩn hóa ──► Cây phân tầng ──► Chunk theo cấu trúc ──► Embedding ──► PostgreSQL
                                                                                                     │
Câu hỏi ──► Nhận diện số hiệu (Điều 3, Khoản 2, Mục 1.2…) ──► tra trực tiếp theo cấu trúc (refs JSONB) ─┤
        └─► Hybrid search (vector + keyword, gộp bằng RRF) ──► Rerank ──► LLM chọn mục + trích nguyên văn
                                                                       └─► Đối chiếu trích dẫn, xác định mục sâu nhất chứa nó
```

### 1. Chuyển sang Markdown (`backend/app/converters.py`)
- PDF: `pymupdf4llm` (nhận diện bố cục, bảng). PDF scan được phát hiện tự động và chạy OCR nếu đã cài engine.
- DOCX: `mammoth` → HTML → `markdownify` (giữ Heading, bảng, danh sách tự đánh số của Word).
- Chuẩn hóa: bỏ số trang, ghép dòng bị ngắt giữa câu, bỏ gạch nối cuối dòng.

### 2. Chunking Hierarchy-Aware (`backend/app/hierarchy.py`)
- Nhận diện tiêu đề bằng mẫu: `Phần thứ…`, `Chương I`, `Mục 1`, `Tiểu mục 1`, `Điều 5.`, khoản `1.`, điểm `a)`, `I.`, `1.1.`, `1.1.1`, heading Markdown.
- Chống nhận nhầm: kiểm tra **tính liên tiếp của số thứ tự** (khoản 2 phải sau khoản 1, điểm b sau điểm a, Điều tăng dần, 1.2 phải nằm trong 1.) và nhận biết danh sách liệt kê trong thân bài; câu như "…theo quy định tại Điều 5 của Luật này" không bị coi là tiêu đề.
- Mỗi nút trong cây được lưu vào bảng `sections` với `path` (breadcrumb), `refs` (ví dụ `{"chapter":"2","article":"3","clause":"2","point":"b"}`), nội dung riêng và toàn văn.
- Chunk = đơn vị nhỏ nhất có tiêu đề (ví dụ một **Điều** gộp luôn các khoản/điểm của nó). Mục quá dài được tách theo đoạn có overlap, mỗi phần vẫn lặp lại tiêu đề. Không bao giờ gộp hai mục anh em vào một chunk.
- Văn bản dùng để embedding được thêm ngữ cảnh: `Tài liệu: … / Vị trí: Chương II > Mục 1 > Điều 3`.

### 3. Truy vấn (`retrieval.py`, `rerank.py`, `rag.py`)
1. **Tra theo số hiệu**: câu hỏi chứa "Điểm b khoản 2 Điều 3" → truy vấn `refs @> {...}` lấy đúng nút.
2. **Hybrid search**: pgvector (cosine, HNSW) + PostgreSQL full-text, gộp bằng Reciprocal Rank Fusion.
3. **Rerank** (`RERANKER`):
   - `llm` (mặc định, không cần cài thêm): OpenAI chấm điểm 0–10 từng ứng viên.
   - `cross_encoder`: `BAAI/bge-reranker-v2-m3` (đa ngôn ngữ, tốt cho tiếng Việt) – cần `pip install sentence-transformers`.
   - `none`: giữ thứ tự RRF.
4. **Sinh câu trả lời**: LLM chỉ được dùng các nguồn, phải trả về trích dẫn nguyên văn. Hệ thống đối chiếu trích dẫn với tài liệu (`quote_verified`) rồi tìm **mục sâu nhất** chứa trích dẫn đó (ví dụ thu hẹp từ Điều 3 xuống Điểm b Khoản 2) và trả về toàn văn mục đó.

## Cài đặt & chạy

Yêu cầu: Python 3.11+, Node.js 18+, Docker (để chạy PostgreSQL + pgvector).

### 1. Cấu hình
```powershell
Copy-Item .env.example .env
```
Điền `OPENAI_API_KEY` trong `.env`.

### 2. PostgreSQL + pgvector
```powershell
docker compose up -d db
```

### 3. Backend
```powershell
python -m venv venv
```
```powershell
venv\Scripts\Activate
```
```powershell
pip install -r backend\requirements.txt
```
```powershell
cd backend; uvicorn app.main:app --reload --port 8000
```
API docs: http://localhost:8000/docs

Hoặc chạy cả DB + backend bằng Docker:
```powershell
docker compose --profile full up -d --build
```

### 4. Frontend
```powershell
cd frontend; npm install; npm run dev
```
Mở http://localhost:5173

Kiểm tra kiểu TypeScript và build production:
```powershell
cd frontend; npm run typecheck; npm run build
```

### CLI tiện ích (chạy trong thư mục `backend`)
```powershell
python -m app.cli preview ..\data\van_ban.docx
```
```powershell
python -m app.cli ingest ..\data
```
```powershell
python -m app.cli ask "What does paragraph 2 of Article 3 provide?"
```
`preview` chỉ chuyển đổi và in cây phân tầng, không cần DB hay OpenAI – nên dùng để kiểm tra tài liệu trước khi nạp.

### Migration schema (Alembic)
Schema PostgreSQL được quản lý bằng Alembic (`backend/alembic.ini`, `backend/migrations/versions/`). Backend tự chạy `alembic upgrade head` khi khởi động. Các migration viết bằng SQL thuần (`op.execute`), không dùng ORM. DB tạo bằng bản cũ (trước khi có Alembic) được migration `0001` nâng cấp thẳng mà không mất dữ liệu.

Các lệnh dưới chạy trong thư mục `backend`. `DATABASE_URL` được lấy từ `.env`.
```powershell
alembic current
```
```powershell
alembic upgrade head
```
Tạo migration mới (sau đó sửa `upgrade()`/`downgrade()` bằng `op.execute("...")`):
```powershell
alembic revision -m "mo ta thay doi"
```
Xem SQL sẽ chạy mà không áp dụng vào DB:
```powershell
alembic upgrade head --sql
```

### Test
```powershell
cd backend; pip install -r requirements-dev.txt; python -m pytest -q tests
```
Test luồng đăng nhập (`tests/test_auth_flow.py`) cần một PostgreSQL **dùng để thử, có thể xóa**. Nếu không đặt `TEST_DATABASE_URL` thì các test này tự bỏ qua:
```powershell
$env:TEST_DATABASE_URL="postgresql://rag:rag@localhost:5432/rag_test"; python -m pytest -q tests
```
Trên máy Windows bị Application Control chặn thư viện native (`pydantic_core`, `psycopg`…), chạy test trong container:
```powershell
docker compose --profile full run --rm --no-deps -v ${PWD}/backend/tests:/app/tests -e ENABLE_OCR=false backend sh -c "pip install -q pytest==9.2.0 && python -m pytest -q tests"
```

## Audit chất lượng nạp tài liệu & embedding

### Audit file (không cần DB hay API)
```powershell
cd backend; python -m app.cli audit ..\data --md audit.md --json audit.json
```
Mỗi tài liệu được chuyển đổi + phân tầng + chunk giống hệt lúc nạp, rồi kiểm tra:

| Kiểm tra | Phát hiện |
| --- | --- |
| Unicode NFC | Chữ Việt dạng tổ hợp rời (NFD) – pipeline tự chuẩn hóa NFC, audit xác nhận Markdown đã chuẩn |
| Chất lượng chữ tiếng Việt | Âm tiết không hợp lệ, ký tự lạ → PDF dùng font TCVN3/VNI hoặc OCR kém |
| Độ đầy đủ nội dung | Tỷ lệ từ của file gốc còn trong Markdown, trang bị mất, từ bị mất nhiều nhất |
| Bảng biểu | Số bảng gốc so với số bảng Markdown |
| Tính liên tục số thứ tự | Thiếu / trùng Điều, Chương, Mục, Tiểu mục |
| Tiêu đề bị bỏ sót | Dòng giống tiêu đề (Chương, Mục, Điều…) nhưng nằm trong thân bài |
| Kích thước mục | Mục lá quá dài (nghi sót tiêu đề con), tiêu đề rỗng (nghi nhận nhầm) |
| Văn bản mồ côi | Tỷ lệ nội dung rơi vào phần mở đầu thay vì vào cấu trúc |
| Header/footer còn sót | Dòng ngắn lặp lại nhiều lần |
| Chunk | Chunk vượt ra ngoài mục của nó, chunk quá dài, phân bố độ dài |

Trạng thái: `PASS` / `INFO` / `WARN` / `FAIL`. Lệnh trả mã lỗi khi có `FAIL` (thêm `--strict` để tính cả `WARN`) – dùng được trong CI.

### Audit dữ liệu đã nạp trong PostgreSQL
```powershell
cd backend; python -m app.cli audit-db --self-retrieval 30
```
- Số chiều vector đúng `EMBEDDING_DIM`, không NaN / vector rỗng, norm ≈ 1 với Titan V2.
- Toàn bộ chunk dùng cùng một model embedding (cột `chunks.embedding_model`); dữ liệu nạp trước khi có cột này sẽ báo `WARN` – xử lý lại tài liệu để ghi model.
- Không có vector trùng nhau cho nội dung khác nhau, mọi chunk nằm gọn trong mục của nó.
- `--self-retrieval N`: lấy N tiêu đề mục ngẫu nhiên làm câu hỏi, đo Hit@1, Hit@5, MRR xem có tìm lại đúng mục đó không (gọi embedding API, tốn một ít chi phí). Thêm `--doc <id>` để audit một tài liệu.

## Đánh giá truy hồi & toàn hệ thống

### 1. Sinh bộ câu hỏi
```powershell
cd backend; python -m app.cli eval-generate ..\data --out ..\eval\datasets\v1.jsonl --sections 20 --per-section 2 --unanswerable 5
```
- Lấy mẫu các mục **không chồng lấn nhau** (không chọn cả mục cha lẫn mục con), phân tầng theo loại (Điểm, Khoản, Điều, mục số…).
- 4 loại câu hỏi: `reference` (sinh theo mẫu từ số hiệu, ví dụ "Điểm b khoản 2 Điều 3 quy định gì?"), `semantic` (diễn đạt lại, không lặp từ khóa), `factoid` (con số, thời hạn, ô trong bảng), `unanswerable` (cùng chủ đề nhưng tài liệu không có).
- Câu hỏi viết đúng ngôn ngữ của tài liệu; đoạn trích (`gold_quote`) phải khớp nguyên văn tài liệu, đáp án chuẩn (`gold_path`) được thu hẹp tới mục sâu nhất chứa đoạn trích; câu trùng và câu chép nguyên văn bị loại.
- Đáp án chuẩn lưu theo **đường dẫn mục**, không theo UUID, nên vẫn dùng được sau khi nạp lại tài liệu.
- **Bắt buộc người duyệt**: LLM có thể sinh câu "không có trong tài liệu" nhưng thực ra có. Sửa/xóa câu chưa đạt, đặt `"reviewed": true`, rồi chạy với `--reviewed-only`.

`--no-llm` chỉ sinh câu `reference` theo mẫu (miễn phí).

### 2. Chạy đánh giá
```powershell
cd backend; python -m app.cli eval-run ..\eval\datasets\v1.jsonl --backend db
```
- `--backend db`: đánh giá đúng dữ liệu đã nạp trong PostgreSQL.
- `--backend memory --docs ..\data`: dựng chỉ mục trong bộ nhớ từ file (cùng parser, chunker, embedding; embedding được cache trong `eval/.cache`) – dùng để so sánh cấu hình (model embedding, `CHUNK_MAX_CHARS`, reranker…) mà không phải nạp lại DB.
- `--stage retrieval|e2e|all`, `--reranker llm|bedrock|none` để so ablation, `--scope document` để chỉ tìm trong tài liệu của câu hỏi, `--no-judge` để bỏ giám khảo LLM, `--strict` để trả mã lỗi khi trượt ngưỡng (dùng cho CI).

Báo cáo lưu ở `eval/reports/<thời điểm>-report.md` (kèm `summary.json` và từng dòng kết quả `*-rows.jsonl` để phân tích lỗi):

| Nhóm | Chỉ số | Ý nghĩa | Ngưỡng |
| --- | --- | --- | --- |
| Truy hồi | Recall@1/3/5/10, Exact@k, MRR@10, nDCG@10 | Đo riêng từng nhánh: vector, keyword, hybrid (RRF), rerank, pipeline (gồm tra số hiệu), reference | pipeline R@5 ≥ 90%, rerank R@5 ≥ 85% |
| E2E | section_exact, granularity | Mục chính xác trả về có đúng mục đáp án không; nếu sai thì rộng hơn / hẹp hơn / sai hẳn | ≥ 75% |
| E2E | quote_verified, quote_f1 | Trích dẫn khớp nguyên văn tài liệu; độ trùng với đoạn trích chuẩn | ≥ 95%, ≥ 80% |
| E2E | citation_match | Số hiệu nêu trong câu trả lời ("Theo Khoản 2 Điều 3") khớp đáp án | ≥ 95% |
| E2E | faithfulness, correctness | Giám khảo LLM chấm mức bám nguồn và độ đúng | ≥ 95% |
| E2E | abstention | Tỷ lệ từ chối đúng với câu không có trong tài liệu | ≥ 80% |
| E2E | latency p50/p95 | Thời gian truy hồi + sinh câu trả lời | theo dõi |

Giám khảo mặc định là `EVAL_JUDGE_MODEL_BEDROCK=au.anthropic.claude-sonnet-5` (mạnh hơn model trả lời để giảm thiên vị). Nếu tài khoản chưa được cấp quyền model này, hệ thống tự lùi về `BEDROCK_LLM_MODEL` và ghi model thực tế vào báo cáo.

## Chạy với AWS Bedrock bằng IAM (test local)

Ứng dụng dùng chuỗi credentials chuẩn của boto3 (`AWS_PROFILE` trong `.env`, hoặc biến môi trường, hoặc `aws login`), không cần OpenAI key.

| Thành phần | Model | Region |
| --- | --- | --- |
| Sinh câu trả lời, rerank `llm` | `au.anthropic.claude-haiku-4-5-20251001-v1:0` (inference profile Úc) | `ap-southeast-2` (định tuyến tới ap-southeast-2 / ap-southeast-4) |
| Embedding | `amazon.titan-embed-text-v2:0`, `EMBEDDING_DIM=1024` | `ap-southeast-2` |
| Rerank `bedrock` | `cohere.rerank-v3-5:0` qua Bedrock Rerank API | `ap-northeast-1` (Sydney chưa có Rerank) |

### 1. Gắn quyền IAM
Policy least-privilege: [infra/iam/bedrock-rag-local-policy.json](infra/iam/bedrock-rag-local-policy.json). Gắn cho IAM user / role dùng để test:
```powershell
aws iam put-user-policy --user-name <ten-user> --policy-name BedrockRagLocal --policy-document file://infra/iam/bedrock-rag-local-policy.json
```
Nếu tài khoản chưa từng dùng model bên thứ ba (Cohere), lần gọi đầu cần quyền Marketplace: [infra/iam/bedrock-marketplace-first-use-policy.json](infra/iam/bedrock-marketplace-first-use-policy.json). Model Anthropic cần gửi form use case một lần trong Bedrock console.

### 2. Cấu hình `.env`
```
LLM_PROVIDER=bedrock
AWS_PROFILE=default
AWS_REGION=ap-southeast-2
EMBEDDING_DIM=1024
RERANKER=llm
```
`RERANKER=bedrock` để dùng Cohere Rerank 3.5 (cần `bedrock:Rerank` không bị SCP chặn ở `ap-northeast-1`).

### 3. Kiểm tra kết nối
```powershell
cd backend; python -m app.cli check-aws --all
```
Lệnh gọi STS, embedding, Converse và Rerank với câu mẫu ngắn và báo OK/FAIL từng bước.

### Lưu ý
- Đổi provider làm đổi số chiều vector (1536 ↔ 1024). Backend sẽ báo lỗi khi khởi động nếu DB cũ không khớp. Khi đó, tạo lại DB bằng `docker compose down -v` rồi `docker compose up -d db`, hoặc viết một migration Alembic đổi kiểu cột `chunks.embedding`. Cách nào cũng phải nạp lại tài liệu.
- Khi chạy backend bằng Docker (`--profile full`), thư mục `~/.aws` được mount read-only vào container; đặt `AWS_DIR` nếu nằm chỗ khác.
- Mọi lệnh gọi Converse đều đặt `maxTokens` (`LLM_MAX_TOKENS`) để tránh giữ chỗ quota quá mức, client dùng adaptive retry.

## Đăng nhập & lịch sử chat

- Tài khoản lưu trong bảng `users`; mật khẩu băm bằng **scrypt** (thư viện chuẩn Python, không cần thư viện native).
- Phiên đăng nhập dùng hai loại token:
  - **Access token**: JWT (HS256), sống ngắn (mặc định 15 phút), gửi qua header `Authorization: Bearer …`. Frontend chỉ giữ token này trong bộ nhớ, không lưu vào `localStorage`.
  - **Refresh token**: chuỗi ngẫu nhiên nằm trong cookie `rag_refresh` (`httpOnly`, `SameSite=Strict`, path `/api/auth`), nên JavaScript trên trang không đọc được. DB chỉ lưu bản băm SHA-256 (bảng `refresh_tokens`, `auth_sessions`, tạo bởi migration `0003`).
- Khi access token hết hạn, frontend tự gọi `POST /api/auth/refresh` rồi gửi lại request. Mỗi refresh token chỉ dùng được một lần và được thay bằng token mới (rotation).
  - Nếu một refresh token đã dùng bị gửi lại sau `REFRESH_REUSE_GRACE_SECONDS`, hệ thống coi là token bị đánh cắp và thu hồi cả phiên.
  - Trong khoảng ân hạn đó (ví dụ hai tab cùng refresh), request vẫn nhận được access token.
- Mỗi lần đăng nhập là một phiên (`auth_sessions`). Access token mang id phiên (`sid`), nên khi phiên bị thu hồi thì access token mất hiệu lực **ngay**, không phải chờ hết hạn:
  - `POST /api/auth/logout`: thu hồi phiên hiện tại.
  - `POST /api/auth/logout-all`: thu hồi mọi phiên của tài khoản.
  - `POST /api/auth/change-password` (`current_password`, `new_password`): đổi mật khẩu và thu hồi mọi phiên khác, giữ phiên đang dùng.
- **Người đăng ký đầu tiên tự động là `admin`**, những người sau là `user`.
- Quyền: mọi API trừ `/api/health` và `/api/auth/*` đều cần đăng nhập; **upload / xử lý lại / xóa tài liệu chỉ admin** được làm. Người dùng thường chỉ xem tài liệu và hỏi đáp.
- Mỗi người chỉ thấy hội thoại của mình (truy cập hội thoại người khác trả 404). Câu trả lời được lưu kèm toàn bộ kết quả (mục chính xác, trích dẫn, nguồn sau rerank) nên mở lại hội thoại cũ hiển thị đầy đủ như lúc hỏi. Tiêu đề hội thoại tự lấy từ câu hỏi đầu tiên, có thể đổi tên / xóa.

Cấu hình trong `.env`:

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `APP_ENV` | development | Khác `development` thì backend **không khởi động** nếu thiếu `JWT_SECRET` hoặc khóa ngắn hơn 32 ký tự |
| `JWT_SECRET` | (trống) | Khóa ký access token. Ở development nếu để trống, backend sinh khóa ngẫu nhiên: mọi người bị đăng xuất khi khởi động lại và không chạy được nhiều worker |
| `JWT_EXPIRE_MINUTES` | 15 | Thời hạn access token |
| `REFRESH_TOKEN_DAYS` | 14 | Phiên tự hết hạn nếu không dùng trong khoảng này (mỗi lần refresh lại gia hạn) |
| `REFRESH_REUSE_GRACE_SECONDS` | 30 | Khoảng ân hạn cho refresh đồng thời trước khi coi là dùng lại token |
| `COOKIE_SECURE` | false | Đặt `true` khi chạy qua HTTPS để cookie refresh chỉ gửi qua HTTPS |
| `ALLOW_REGISTRATION` | true | `false` để chỉ admin tạo tài khoản (tài khoản đầu tiên vẫn đăng ký được) |
| `MIN_PASSWORD_LENGTH` | 8 | Độ dài mật khẩu tối thiểu |

Tạo khóa ngẫu nhiên:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Tạo tài khoản bằng dòng lệnh (khi tắt tự đăng ký):
```powershell
docker compose --profile full exec backend python -m app.cli create-user ten_dang_nhap --display-name "Họ Tên"
```
Thêm `--admin` để cấp quyền quản trị.

## Metadata tài liệu & tự giới hạn phạm vi tìm kiếm

Khi nạp tài liệu, hệ thống trích metadata (lưu ở cột `documents.meta`):

| Nguồn | Lấy được |
| --- | --- |
| Regex trên nội dung | DOI, arXiv ID, ISBN |
| Metadata nhúng trong PDF | Tiêu đề, tác giả (bỏ giá trị rác kiểu "Microsoft Word - …") |
| LLM đọc ~8.000 ký tự đầu (trang tiêu đề, abstract, trang bản quyền) | Tiêu đề, tên ngắn, tác giả, năm, loại (`paper`/`book`/`report`/`manual`), nơi xuất bản, tên gọi khác, tóm tắt 1 câu |

Metadata được dùng ở 2 chỗ:
1. **Ngữ cảnh embedding**: mỗi chunk mở đầu bằng `Tài liệu: Attention Is All You Need (Vaswani et al., 2017)` thay vì tên file.
2. **Tự giới hạn phạm vi**: nếu câu hỏi nhắc tới tài liệu – tiêu đề, tên ngắn/tên gọi khác ("the Transformer paper"), tác giả ("Vaswani et al.", "Vaswani 2017", "Robert C. Martin"), DOI/arXiv/ISBN – hệ thống chỉ tìm trong tài liệu đó (kết quả có `scope: "detected"` và `documents_detected`). Người dùng tự chọn tài liệu thì lựa chọn đó được ưu tiên (`scope: "selected"`); không nhắc gì thì tìm toàn bộ (`scope: "all"`).

Chống nhận nhầm: tên gọi một từ chỉ được chấp nhận khi là viết tắt (BERT), CamelCase (ResNet) hoặc chính là tiêu đề một từ; tiêu đề một từ ("Refactoring") chỉ khớp khi viết hoa và không đứng đầu câu, nên "When is refactoring worth it?" không bị gán cho cuốn sách.

Sửa metadata: admin mở tài liệu → tab **Thông tin** → **Sửa metadata** (hoặc `PATCH /api/documents/{id}/metadata`). Metadata sửa tay được đánh dấu `manual`, không bị ghi đè khi xử lý lại; nhận diện trong câu hỏi dùng ngay, còn ngữ cảnh embedding cập nhật sau khi bấm **Xử lý lại**.

Bổ sung metadata cho tài liệu đã nạp trước đây (không embedding lại):
```powershell
docker compose --profile full exec backend python -m app.cli extract-metadata
```
Thêm `--doc <id>` cho một tài liệu, `--force` để ghi đè cả metadata đã sửa tay. Đặt `METADATA_LLM=false` để chỉ dùng regex + metadata PDF (không gọi LLM).

## API chính

| Method | Endpoint | Quyền | Mô tả |
| --- | --- | --- | --- |
| POST | `/api/auth/register` | – | `{"username", "password", "display_name"}` → token |
| POST | `/api/auth/login` | – | `{"username", "password"}` → `{"access_token", "user"}` |
| GET | `/api/auth/me` | user | Thông tin người dùng hiện tại |
| GET | `/api/conversations` | user | Danh sách hội thoại của mình |
| POST | `/api/conversations` | user | Tạo hội thoại |
| GET | `/api/conversations/{id}` | user | Hội thoại kèm toàn bộ tin nhắn |
| PATCH / DELETE | `/api/conversations/{id}` | user | Đổi tên / xóa |
| POST | `/api/conversations/{id}/messages` | user | `{"query", "document_ids"}` → hỏi, lưu câu hỏi + câu trả lời |
| GET | `/api/documents`, `/api/documents/{id}/tree`, `/markdown`, `/api/sections/{id}` | user | Xem tài liệu |
| POST | `/api/documents` | admin | Upload PDF/DOCX (multipart `files`), xử lý nền |
| POST | `/api/documents/{id}/reindex` | admin | Xử lý lại |
| DELETE | `/api/documents/{id}` | admin | Xóa |
| PATCH | `/api/documents/{id}/metadata` | admin | Sửa metadata (tiêu đề, tác giả, năm, DOI/ISBN, tên gọi khác…) |
| POST | `/api/query` | user | Hỏi không lưu lịch sử: `{"query", "document_ids", "top_k"}` |

Kết quả `/api/query` (và trường `result` của tin nhắn trả lời) gồm `answer`, `primary` (mục chính xác: `path`, `content` nguyên văn, `quote`, `quote_verified`) và `sources` (các mục sau rerank kèm điểm vector / keyword / RRF / rerank).

## Xử lý sự cố

### `ModuleNotFoundError: No module named 'app'`
Package `app` nằm trong `backend/`. Chạy `uvicorn` từ thư mục `backend`:
```powershell
cd backend; uvicorn app.main:app --reload
```

### `ImportError: no pq wrapper available … An Application Control policy has blocked this file`
Windows (Smart App Control / Application Control) chặn các DLL chưa ký của `psycopg-binary` (`libpq`, `libssl`, `libcrypto`). Không sửa được bằng code – chạy backend trong Docker:
```powershell
docker compose --profile full up -d --build
```
API: http://localhost:8000/docs. Khi sửa code backend, build lại bằng lệnh trên.

### `npm run dev` báo `An Application Control policy has blocked this file … rollup.win32-x64-msvc.node`
Cùng nguyên nhân với `psycopg`: Windows chặn binary native chưa ký của rollup/esbuild. `package.json` đã dùng `overrides` để thay bằng bản WebAssembly (`@rollup/wasm-node`, `esbuild-wasm`), nên không còn file native nào. Nếu đã cài từ trước, cài lại sạch:
```powershell
cd frontend; Remove-Item -Recurse -Force node_modules, package-lock.json; npm install
```
esbuild-wasm xóa biến môi trường (kể cả `SystemRoot`) trước khi chạy, làm OpenSSL của Node 26 không lấy được entropy (`ERR_OSSL_ENTROPY_SOURCE_STRENGTH_TOO_WEAK`). Các script `dev`/`build`/`preview` nạp trước `scripts/seed-entropy.cjs` để khởi tạo bộ sinh số ngẫu nhiên trước bước đó – luôn chạy qua `npm run …`, không gọi `vite` trực tiếp.

### Container gọi Bedrock bị `ReadTimeoutError`
Triệu chứng: request nhỏ thì được, payload vài KB trở lên treo. Nguyên nhân là MTU: đường mạng thực tế nhỏ hơn 1500 nhưng NAT của Docker/WSL không tự dò được, gói lớn bị rớt. `docker-compose.yml` đặt MTU mạng Docker = 1400 (đổi bằng biến `DOCKER_MTU` trong `.env`, ví dụ 1300 nếu vẫn lỗi). Sau khi đổi:
```powershell
docker compose --profile full down; docker compose --profile full up -d
```
Kiểm tra từ trong container:
```powershell
docker compose --profile full exec backend python -m app.cli check-aws
```

## PDF scan (ví dụ `data/TT_HCM.pdf`)
File scan không có lớp text nên cần OCR. Cài thêm `pip install rapidocr onnxruntime` (hoặc Tesseract kèm gói ngôn ngữ `vie`) rồi bật `ENABLE_OCR=true`. Nếu không có OCR, tài liệu sẽ báo lỗi rõ ràng thay vì nạp nội dung rỗng. Với văn bản tiếng Việt có dấu, Tesseract `vie` thường cho kết quả tốt hơn.

## Cấu hình (`.env`)

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `CHUNK_MAX_CHARS` | 2000 | Kích thước tối đa một chunk; mục dài hơn sẽ được tách |
| `LLM_PROVIDER` | `openai` | `bedrock` (IAM) hoặc `openai` |
| `RERANKER` | `llm` | `llm` / `bedrock` / `cross_encoder` / `none` |
| `VECTOR_TOP_K`, `KEYWORD_TOP_K` | 30 | Số ứng viên mỗi nhánh tìm kiếm |
| `RERANK_CANDIDATES` | 20 | Số mục đưa vào rerank |
| `FINAL_TOP_K` | 5 | Số mục trả về |
| `EMBEDDING_DIM` | 1536 | Phải ≤ 2000 để dùng index HNSW |
