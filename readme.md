# Hierarchy-Aware RAG cho tài liệu tiêu chuẩn & văn bản luật

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
python -m app.cli ask "Khoản 2 Điều 3 quy định gì?"
```
`preview` chỉ chuyển đổi và in cây phân tầng, không cần DB hay OpenAI – nên dùng để kiểm tra tài liệu trước khi nạp.

### Test
```powershell
cd backend; pip install -r requirements-dev.txt; python -m pytest -q tests
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
- Đổi provider làm đổi số chiều vector (1536 ↔ 1024). Backend sẽ báo lỗi khi khởi động nếu DB cũ không khớp; tạo lại DB bằng `docker compose down -v` rồi `docker compose up -d db` và nạp lại tài liệu.
- Khi chạy backend bằng Docker (`--profile full`), thư mục `~/.aws` được mount read-only vào container; đặt `AWS_DIR` nếu nằm chỗ khác.
- Mọi lệnh gọi Converse đều đặt `maxTokens` (`LLM_MAX_TOKENS`) để tránh giữ chỗ quota quá mức, client dùng adaptive retry.

## API chính

| Method | Endpoint | Mô tả |
| --- | --- | --- |
| POST | `/api/documents` | Upload PDF/DOCX (multipart `files`), xử lý nền |
| GET | `/api/documents` | Danh sách tài liệu + trạng thái |
| GET | `/api/documents/{id}/tree` | Cây phân tầng |
| GET | `/api/documents/{id}/markdown` | Markdown đã chuẩn hóa |
| GET | `/api/sections/{id}` | Toàn văn một mục |
| POST | `/api/documents/{id}/reindex` | Xử lý lại |
| DELETE | `/api/documents/{id}` | Xóa |
| POST | `/api/query` | `{"query": "...", "document_ids": [...], "top_k": 5}` |

Kết quả `/api/query` gồm `answer`, `primary` (mục chính xác: `path`, `content` nguyên văn, `quote`, `quote_verified`) và `sources` (các mục sau rerank kèm điểm vector / keyword / RRF / rerank).

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
