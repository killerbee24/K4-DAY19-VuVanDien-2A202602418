# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Vũ Văn Diện

**MSSV:** 2A202602418

**Ngày:** 05/10/2026

**Cấu hình:** MWAPI `claude-haiku-4-5-20251001`, Gemini `gemini-embedding-001`, `top_k=3`, `chunk_size=800`, 176 chunks. Knowledge Graph đầy đủ có 210 node và 396 relationship.

## 1. Chi phí (10 điểm)

Kết quả nguyên văn từ `ket_qua_benchmark_kg.txt`:

```text
== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176         0        0   0.00000    108.9
graph       196    153668     7349   0.95207    207.1

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.51   1.33     5633      142   0.03171     3.62
graph       0.94   2.00    13827      190   0.07388     5.20
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | ---: | ---: | ---: |
| Indexing USD | 0,00000 | 0,95207 | N/A vì Flat được ghi nhận 0 USD |
| Indexing giây | 108,9 | 207,1 | ×1,90 |
| Mỗi câu: USD | 0,03171 | 0,07388 | ×2,33 |
| Mỗi câu: giây | 3,62 | 5,20 | ×1,44 |
| Mỗi câu: input token | 5.633 | 13.827 | ×2,45 |

Chi phí indexing tăng thêm của GraphRAG là 0,95207 USD và 98,2 giây, chủ yếu do 20 lượt LLM trích xuất bài báo thành entity/relationship. Mỗi câu GraphRAG tốn thêm 0,04217 USD, 1,58 giây và khoảng 8.194 input token vì prompt chứa cả chunk lẫn graph facts. Flat indexing hiện 0 USD vì response embedding tương thích OpenAI của Gemini không trả token usage cho chương trình; đây là giới hạn phép đo, không khẳng định embedding thực tế miễn phí.

Không có điểm hòa vốn thuần chi phí trong lần đo này vì GraphRAG vừa có chi phí dựng cao hơn vừa có chi phí mỗi câu cao hơn. Giá trị bù lại là chất lượng: recall trung bình tăng từ 0,51 lên 0,94 và judge tăng từ 1,33 lên 2,00.

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao |
| --- | --- | ---: | ---: | --- | --- |
| Q1 | single-hop-law | 1,00 / 2 | 1,00 / 2 | Hòa | Định nghĩa nằm gọn trong một chunk luật; graph không bổ sung lợi ích đáng kể. |
| Q2 | single-hop-news | 1,00 / 2 | 1,00 / 2 | Hòa | Tên hai bị cáo và án tử hình cùng nằm trong một bài báo. |
| Q3 | cross-kb | 0,33 / 1 | 0,67 / 2 | Graph | Flat có 36 tháng và tội danh nhưng thiếu Điều/khung cơ bản; graph nối sang Điều 251 khoản 1. |
| Q4 | cross-kb | 0,33 / 1 | 1,00 / 2 | Graph | Graph đi qua `Crime` tới Điều 255 và lấy toàn bộ khoản khi câu hỏi hỏi mức tối đa. |
| Q5 | cross-kb-multi-hop | 0,40 / 1 | 1,00 / 2 | Graph | Graph ghép người-vụ-tội-Điều-khoản và đối chiếu MDMA 9,6 kg với ngưỡng khoản 4. |
| Q6 | aggregation | 0,00 / 1 | 1,00 / 2 | Graph | Seed `MDMA` mở rộng qua tất cả cạnh `INVOLVES`, trong khi top-3 chunk của Flat không bao phủ đủ ba nhóm vụ. |

Quy luật quan sát được: Flat RAG đủ cho câu single-hop khi mọi ý nằm trong cùng nguồn; GraphRAG thắng rõ ở câu xuyên hai KB và aggregation. Q3 có judge 2 dù recall chỉ 0,67 do phép đo keyword yêu cầu đúng định dạng số `02/07`, được phân tích ở lỗi E4.

## 3. Phân tích lỗi (20 điểm)

### Lỗi E3 — Trùng thực thể do khóa tên phân biệt hoa/thường và tên do LLM đặt

- **Hiện tượng:** cùng chất etomidate tồn tại thành hai node `etomidate` và `Etomidate`. Cùng vụ vận chuyển của Cái Quang Huy cũng xuất hiện dưới hai tên Case khác nhau.
- **Bằng chứng:**

```cypher
MATCH (s:Substance)
RETURN s.name AS name
ORDER BY toLower(s.name);
```

Kết quả liên quan:

```text
"etomidate"
"Etomidate"
```

```cypher
MATCH (k:Case)
WHERE k.name CONTAINS 'Berlin' OR k.name CONTAINS '9,6kg'
RETURN k.name AS case_name, k.doc_id AS doc_id
ORDER BY doc_id;
```

```text
"Vụ vận chuyển hơn 9,6kg MDMA và 406g Ketamine từ Berlin về Việt Nam" | news-100260917203001265
"Vụ vận chuyển ma túy từ Berlin qua sân bay Nội Bài"                 | news-100260918080821054
```

- **Nguyên nhân:** `add_news_case` dùng `MERGE` trực tiếp theo chuỗi `Substance.name` và `Case.name`. `extract_news_cases` chuẩn hóa tội danh nhưng chưa đưa tên chất qua `link_entity`; tên Case hoàn toàn do LLM sinh. Neo4j coi chuỗi khác hoa/thường là hai khóa khác nhau. Ngoài ra corpus có đoạn tin liên quan được lặp trong nhiều file nên LLM tạo hai tên cho cùng một vụ.
- **Đề xuất sửa:** trước khi ghi graph, chuẩn hóa chất bằng bảng canonical và `link_entity` với hàm normalize `casefold`/chuẩn hóa khoảng trắng; lưu `display_name` riêng. Với Case, dùng khóa ổn định hơn như hash của tập người-ngày-địa điểm hoặc thêm bước entity resolution giữa các bài. Đánh đổi là tăng code, có nguy cơ gộp nhầm và có thể cần thêm LLM/token để so khớp vụ.

### Lỗi E4 — Keyword recall chấm thấp câu trả lời đúng

- **Hiện tượng:** Q3 GraphRAG được LLM judge chấm đúng đủ (`2`) nhưng keyword recall chỉ `0,67`.
- **Bằng chứng:** `must_include` của Q3 yêu cầu nguyên chuỗi `"02 năm đến 07 năm"`, còn câu trả lời GraphRAG là:

```text
Tội này được quy định tại Điều 251 BLHS với khung hình phạt cơ bản là từ 2 năm đến 7 năm tù (khoản 1).
```

Kết quả trong file benchmark:

```text
Q3 graph recall=0.67 judge=2
```

- **Nguyên nhân:** `keyword_recall` dùng phép kiểm tra substring sau khi lowercase, không chuẩn hóa số có số 0 ở đầu (`02` so với `2`), dấu câu hay cách diễn đạt tương đương. Câu trả lời có đủ ba ý về nội dung nhưng trượt một keyword hình thức.
- **Đề xuất sửa:** chuẩn hóa số và khoảng trắng trước khi so; tách `must_include` thành nhóm biến thể chấp nhận được; hoặc báo cáo đồng thời exact recall và semantic judge thay vì dùng một số duy nhất. Dùng judge giúp hiểu nghĩa nhưng tốn thêm token, có độ dao động và bản thân judge cũng có thể sai.

## 4. Kết luận (5 điểm)

Flat RAG phù hợp khi câu hỏi là single-hop và đáp án nằm gọn trong một đoạn: Q1 và Q2 đều đạt recall 1,00, judge 2 với chi phí trung bình thấp hơn. Knowledge Graph đáng dùng khi phải nối tin với luật, đi nhiều bước hoặc tổng hợp nhiều tài liệu: Q3–Q6 đều được GraphRAG chấm 2; Q4–Q6 đạt recall 1,00, trong khi Flat chỉ đạt lần lượt 0,33; 0,40; 0,00.

Đổi lại, GraphRAG trong lần đo này tốn khoảng 2,33 lần USD và 1,44 lần thời gian cho mỗi câu, cộng 0,95207 USD chi phí dựng graph. Vì vậy nên dùng KG khi độ chính xác của câu cross-KB/aggregation có giá trị cao hơn chi phí tăng thêm, dữ liệu có entity cầu nối tương đối ổn định và hệ thống phục vụ đủ nhiều câu loại này. Với câu tra cứu trực tiếp trên một tài liệu, Flat RAG đơn giản và kinh tế hơn.

## 5. Tự kiểm (5 điểm)

```text
$ python -m pytest tests/ -q
................................................                         [100%]
48 passed in 0.07s
```

```text
$ python bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = mwapi:claude-haiku-4-5-20251001 | embedding = gemini:gemini-embedding-001
[OK] KG-2 build_graph: 148 node / 294 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 23 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.06549. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Ảnh Neo4j:

- `report/img/kg_count.png`
- `report/img/kg_cross_kb.png`
- `report/img/kg_my_case.png`

Người đã chọn cho `kg_my_case.png`: **Cái Quang Huy**.

## Vấn đề gặp phải (không tính điểm)

- Môi trường ban đầu chưa nhận lệnh `python`/`py`; đã tạo `.venv` Python 3.11.16 bằng `uv` và cài `requirements.txt`.
- Docker daemon ban đầu chưa chạy; đã khởi động Docker Desktop, tải image `neo4j:5` và tạo container `neo4j-drug-kg`.
- MWAPI là gateway Anthropic-compatible nên cần base URL không có `/v1`; request test đã trả đúng model `claude-haiku-4-5-20251001`.
- Claude Haiku 4.5 không nhận `output_config.effort`; nhánh MWAPI dùng stable Messages API và giới hạn `max_tokens=4096`.
- Gemini embedding endpoint không trả token usage theo trường mà benchmark đọc, nên Flat indexing hiển thị 0 token/0 USD; báo cáo không diễn giải số này là miễn phí thực tế.
