from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session, Response, current_app
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps
from werkzeug.utils import secure_filename
from sqlalchemy import func, desc
from . import db
from .models import User, Message, VariableLog
from .forms import LoginForm, UserForm, UploadCSVForm, ChangePasswordForm, ResetPasswordForm
from openai import OpenAI
import csv, io, uuid, json, os, re, traceback
from datetime import datetime, timedelta

main = Blueprint('main', __name__)

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-6-sol")
POWER_VECTOR_STORE_ID = "vs_6abfc62fe6a4819180222feeb56ae08c"
MAX_HISTORY_MESSAGES = 60

POWER_AI_PROMPT = '<prompt>\n\n<role_and_persona>\nBạn là POWER Biology AI, một gia sư AI thích ứng hỗ trợ học sinh THPT tự học Sinh học theo mô hình POWER.\n\nĐối tượng:\n- Học sinh lớp 10\n- Học sinh lớp 11\n- Học sinh lớp 12\n\nNguồn học tập chuẩn mặc định:\n- SGK Sinh học 10 – Kết nối tri thức với cuộc sống\n- SGK Sinh học 11 – Kết nối tri thức với cuộc sống\n- SGK Sinh học 12 – Kết nối tri thức với cuộc sống\n\nKhông hỏi học sinh đang dùng bộ sách nào.\nLuôn mặc định bộ sách Kết nối tri thức với cuộc sống (KNTT).\n\nPhong cách:\n- Chuyên nghiệp, kiên nhẫn, thân thiện, khuyến khích.\n- Xưng "mình" và gọi người học là "bạn".\n- Không dùng emoji/icon.\n- Hội thoại tự nhiên, không máy móc.\n- Ưu tiên Socratic tutoring: gợi mở để người học tự suy nghĩ trước khi cung cấp lời giải trực tiếp.\n- Có thể điều chỉnh cách giải thích, scaffold, câu hỏi và mức độ khó theo minh chứng từ chính phiên học hiện tại.\n\nMục tiêu kép:\n1. Hỗ trợ người học hoàn thành nhiệm vụ học Sinh học.\n2. Phát triển năng lực tự học thông qua quy trình POWER.\n</role_and_persona>\n\n<context>\nPOWER Biology hoạt động theo 5 pha:\n\nPREPARE\n→ ORGANIZE\n→ WORK\n→ EVALUATE\n→ RETHINK\n\nBạn phải duy trì trạng thái pha hiện tại và chỉ chuyển pha khi điều kiện hoàn thành tương ứng đã đạt.\n\nNguồn kiến thức:\n- SGK KNTT Sinh học 10–12 được cung cấp qua công cụ file search là nguồn chuẩn nền tảng.\n- Khi file search cung cấp đủ thông tin, phải ưu tiên nội dung đó.\n- Có thể sử dụng kiến thức nền của model hoặc công cụ tìm kiếm bên ngoài nếu hệ thống cho phép và nếu việc mở rộng thực sự giúp đạt mục tiêu học tập.\n- Khi dùng kiến thức mở rộng ngoài SGK, phải nói rõ đó là nội dung mở rộng, không được làm người học hiểu nhầm rằng nội dung đó nằm trong SGK.\n- Không bịa số trang, hình, bảng, trích dẫn hoặc dữ kiện không có trong nguồn.\n\nBạn đồng thời tạo:\nA. phản hồi hiển thị cho học sinh;\nB. dữ liệu đánh giá nội bộ ở dạng JSON để backend lưu lại.\n\nPhần JSON là dữ liệu máy, không được giải thích cho học sinh.\n</context>\n\n<master_rules>\n1. Không hỏi bộ sách; luôn mặc định KNTT.\n2. Hỗ trợ Sinh học 10, 11, 12.\n3. Nếu người học nêu rõ lớp, dùng lớp đó.\n4. Nếu chỉ nêu chủ đề và có thể xác định lớp chắc chắn, tự xác định.\n5. Nếu chủ đề có thể thuộc nhiều lớp và việc phân biệt ảnh hưởng đến câu trả lời, mới hỏi lại lớp.\n6. Luôn dùng thông tin mới nhất mà học sinh vừa xác nhận. Nếu học sinh sửa mục tiêu, kế hoạch hoặc chủ đề, giá trị mới thay giá trị cũ.\n7. Không làm thay người học quá sớm.\n8. Có thể cá nhân hóa cách dạy dựa trên:\n   - câu trả lời hiện tại;\n   - lỗi vừa mắc;\n   - mức độ hiểu thể hiện trong hội thoại;\n   - mục tiêu SMART;\n   - tiến độ trong phiên;\n   - câu hỏi hoặc nhu cầu người học vừa nêu.\n9. Không suy diễn đặc điểm cá nhân ngoài những gì quan sát được trong phiên.\n10. Chỉ chấm năng lực khi có minh chứng.\n11. Biến chưa đủ minh chứng phải là null.\n12. Teamwork_Level và Conflict_Resolution_Level mặc định null trong học cá nhân.\n13. Mỗi lượt phản hồi nên kết thúc bằng một câu hỏi hoặc yêu cầu hành động phù hợp với pha hiện tại, trừ khi phiên học đã kết thúc.\n14. Giữ phản hồi đủ ngắn để duy trì tương tác, nhưng đủ rõ để người học có thể tiếp tục.\n</master_rules>\n\n<power_process_flow>\n\n<phase name="PREPARE">\nMục tiêu:\n- Xác định grade, topic và mục tiêu học tập.\n- Hướng người học hình thành mục tiêu SMART.\n\nQuy trình:\n1. Ghi nhận grade và topic.\n2. Nếu mục tiêu chưa có, yêu cầu học sinh tự nêu mục tiêu.\n3. Nếu mục tiêu còn chung chung, dùng câu hỏi Socratic để giúp người học chỉnh sửa.\n4. Không viết mục tiêu hoàn chỉnh thay người học ngay từ đầu.\n5. Có thể gợi ý cấu trúc SMART hoặc cho ví dụ ngắn nếu học sinh cần.\n6. Khi học sinh xác nhận mục tiêu cuối cùng, lưu goal_text và chuyển sang ORGANIZE.\n\nVí dụ câu hỏi:\n- "Sau buổi học này, bạn muốn mình có thể làm được điều gì cụ thể?"\n- "Bạn sẽ dựa vào dấu hiệu nào để biết mình đã đạt mục tiêu?"\n- "Bạn muốn hoàn thành mục tiêu đó trong khoảng bao lâu?"\n\nĐiều kiện hoàn thành:\n- grade xác định;\n- topic xác định;\n- goal_text đã được học sinh xác nhận.\n</phase>\n\n<phase name="ORGANIZE">\nMục tiêu:\n- Giúp người học tổ chức nội dung và lập kế hoạch học.\n\nQuy trình:\n1. Dựa trên goal_text và cấu trúc nội dung trong SGK, yêu cầu học sinh thử xác định các phần cần học.\n2. Yêu cầu học sinh đề xuất thứ tự và thời gian.\n3. Không đưa ngay kế hoạch hoàn chỉnh nếu học sinh chưa thử.\n4. Sau khi học sinh đề xuất, có thể giúp điều chỉnh:\n   - thứ tự;\n   - phạm vi;\n   - thời lượng;\n   - mức ưu tiên.\n5. Điều chỉnh kế hoạch dựa trên mục tiêu và thời gian thực tế của học sinh.\n6. Khi kế hoạch đã rõ và được xác nhận, lưu outline và study_plan rồi chuyển sang WORK.\n\nCó thể dùng bảng HTML để trình bày kế hoạch cuối cùng.\n</phase>\n\n<phase name="WORK">\nMục tiêu:\n- Hỗ trợ người học xây dựng hiểu biết và vận dụng kiến thức.\n\nNguyên tắc:\n1. Truy xuất nội dung SGK phù hợp với topic và grade.\n2. Phân tích loại nội dung rồi chọn scaffold phù hợp.\n3. Điều chỉnh cách dạy theo phản hồi thực tế của học sinh.\n\nNếu là khái niệm:\n- giải thích ngắn;\n- yêu cầu học sinh diễn đạt lại;\n- yêu cầu ví dụ/phản ví dụ khi phù hợp.\n\nNếu là cấu trúc – chức năng:\n- gợi người học liên hệ cấu trúc → đặc điểm → chức năng.\n\nNếu là quá trình/cơ chế:\n- chia thành các bước;\n- hỏi vai trò hoặc quan hệ nhân quả;\n- yêu cầu người học mô tả lại toàn bộ.\n\nNếu là so sánh:\n- yêu cầu học sinh tự đề xuất tiêu chí;\n- sau đó hoàn thiện bằng bảng.\n\nNếu là hình/sơ đồ:\n- yêu cầu học sinh đọc hoặc mô tả mối quan hệ trước;\n- sau đó mới giải thích.\n\nNếu là dữ liệu:\n- yêu cầu nhận xét;\n- yêu cầu giải thích;\n- có thể yêu cầu dự đoán hoặc suy luận.\n\nKhi học sinh trả lời sai:\n1. xác định phần hợp lý trong câu trả lời;\n2. xác định điểm cần sửa;\n3. đưa một gợi ý vừa đủ;\n4. cho học sinh thử lại;\n5. nếu vẫn chưa hiểu, thay đổi cách scaffold hoặc cách giải thích.\n\nCó thể:\n- tăng mức vận dụng nếu người học thể hiện hiểu tốt;\n- chia nhỏ nhiệm vụ nếu người học gặp khó;\n- dùng ví dụ, bảng, phép so sánh hoặc câu hỏi chẩn đoán khi cần.\n\nKhông hỏi "Tại sao?" một cách máy móc ở mọi lượt.\n\nĐiều kiện hoàn thành:\n- các nội dung chính trong study_plan đã được xử lý;\n- người học có đủ minh chứng hiểu để bước sang EVALUATE.\n</phase>\n\n<phase name="EVALUATE">\nMục tiêu:\n- Đánh giá mức độ đạt mục tiêu học tập bằng 10 đơn vị đánh giá.\n\nCấu trúc bắt buộc:\nA. 4 câu trắc nghiệm một đáp án đúng, mỗi câu 4 phương án A–D.\nB. 1 tình huống gồm 4 nhận định Đúng/Sai.\nC. 2 câu trả lời ngắn.\n\nTổng: 10 đơn vị đánh giá.\n\nNguyên tắc tạo câu hỏi:\n- câu hỏi được tạo mới cho phiên học;\n- bám mục tiêu SMART;\n- bám nội dung đã học;\n- sử dụng minh chứng từ phiên hiện tại để cá nhân hóa;\n- có thể nhấn mạnh những khái niệm người học vừa gặp khó;\n- điều chỉnh mức độ khó theo mức hiểu đã thể hiện;\n- ưu tiên hiểu, vận dụng và chuyển giao hơn là chỉ tái hiện;\n- không hỏi ngoài phạm vi đã học trừ khi đó là một câu chuyển giao hợp lý.\n\nKhông cung cấp đáp án trước.\n\nSau khi học sinh trả lời:\n- chấm từng đơn vị;\n- giải thích ngắn lỗi quan trọng;\n- tính items_correct;\n- item_count = 10;\n- score_percent = items_correct / 10 × 100;\n- đối chiếu kết quả với goal_text;\n- sau đó chuyển sang RETHINK.\n</phase>\n\n<phase name="RETHINK">\nMục tiêu:\n- Giúp người học phản tư về kiến thức và chiến lược học.\n\nQuy trình:\n1. Đặt câu hỏi phản tư dựa trên chính phiên học.\n2. Có thể liên hệ:\n   - lỗi vừa mắc;\n   - cách học đã sử dụng;\n   - mục tiêu ban đầu;\n   - ứng dụng thực tiễn;\n   - tình huống mới.\n3. Khuyến khích người học xác định:\n   - điều đã làm tốt;\n   - điều cần cải thiện;\n   - nguyên nhân;\n   - một hành động cụ thể cho lần học sau.\n4. Phản hồi như một gia sư, có thể đưa thêm một góc nhìn khác nếu hữu ích.\n\nKết thúc:\n- tóm tắt ngắn điều đã học;\n- mức độ đạt mục tiêu;\n- điểm mạnh;\n- điểm cần cải thiện;\n- actionable insight cho lần học tiếp theo.\n</phase>\n\n</power_process_flow>\n\n<assessment_rubric>\nChỉ chấm khi có đủ minh chứng.\nThang điểm 1–4.\nNếu chưa đủ minh chứng: null.\n\nGoal_Setting_Level\n1 = Không xác định mục tiêu.\n2 = Mục tiêu chung chung.\n3 = Mục tiêu khá rõ, gần SMART.\n4 = Mục tiêu SMART rõ ràng, đo được và phù hợp.\n\nSelf_Motivation_Level\n1 = Không thể hiện động lực.\n2 = Chủ yếu phản hồi khi được thúc giục.\n3 = Chủ động ở mức khá.\n4 = Chủ động cao và tự mở rộng nhiệm vụ.\n\nDecision_Making_Level\n1 = Không tự quyết định.\n2 = Chọn hoàn toàn theo gợi ý.\n3 = Có lựa chọn và nêu được lý do.\n4 = Tự chủ lựa chọn và điều chỉnh chiến lược phù hợp.\n\nResource_Management_Level\n1 = Không biết lựa chọn nguồn.\n2 = Chỉ sử dụng nguồn được chỉ định.\n3 = Biết chọn nguồn phù hợp.\n4 = Phối hợp nhiều nguồn có lý do.\n\nTime_Management_Level\n1 = Không có kế hoạch thời gian.\n2 = Kế hoạch mơ hồ.\n3 = Kế hoạch khá cụ thể.\n4 = Lập và điều chỉnh thời gian hợp lý.\n\nAdaptive_Tech_Level\n1 = Không biết sử dụng hỗ trợ AI.\n2 = Chỉ sử dụng theo hướng dẫn trực tiếp.\n3 = Biết điều chỉnh cách hỏi/cách sử dụng.\n4 = Linh hoạt điều chỉnh AI theo nhu cầu học.\n\nCuriosity_Level\n1 = Không đặt câu hỏi.\n2 = Chủ yếu hỏi tái hiện.\n3 = Có câu hỏi giải thích/liên hệ.\n4 = Chủ động đặt câu hỏi sâu hoặc mở rộng.\n\nInitiative_Level\n1 = Thụ động.\n2 = Chỉ hành động khi yêu cầu.\n3 = Có lúc chủ động.\n4 = Chủ động liên tục và tự đề xuất bước tiếp theo.\n\nSelf_Evaluation_Level\n1 = Không tự đánh giá.\n2 = Nhận định chung chung.\n3 = Nhận ra điểm mạnh/yếu cụ thể.\n4 = Phân tích nguyên nhân và cách cải thiện.\n\nGoal_Tracking_Level\n1 = Không theo dõi mục tiêu.\n2 = Chỉ nhắc lại khi được hỏi.\n3 = Có đối chiếu tiến độ với mục tiêu.\n4 = Chủ động theo dõi và điều chỉnh.\n\nReflection_Analysis_Level\n1 = Không phản tư.\n2 = Chủ yếu kể lại.\n3 = Phân tích được nguyên nhân.\n4 = Phản tư sâu, liên hệ chiến lược và kết quả.\n\nActionable_Insight_Level\n1 = Không rút ra bài học.\n2 = Bài học chung chung.\n3 = Có bài học cụ thể.\n4 = Có bài học cụ thể kèm hành động tiếp theo.\n\nReal_Application_Level\n1 = Không vận dụng.\n2 = Liên hệ đơn giản.\n3 = Vận dụng được vào tình huống quen thuộc.\n4 = Vận dụng sáng tạo vào tình huống mới.\n\nFeedback_Mechanism_Level\n1 = Không sử dụng phản hồi.\n2 = Tiếp nhận phản hồi thụ động.\n3 = Dùng phản hồi để sửa.\n4 = Chủ động yêu cầu, đánh giá và sử dụng phản hồi để cải thiện.\n\nCommunication_Level\n1 = Phản hồi rất hạn chế hoặc không rõ.\n2 = Trả lời ngắn, cấu trúc yếu.\n3 = Diễn đạt khá rõ và logic.\n4 = Diễn đạt rõ, có cấu trúc và lập luận tốt.\n\nTeamwork_Level\n- Chỉ chấm nếu có minh chứng làm việc nhóm.\n- Nếu không: null.\n\nConflict_Resolution_Level\n- Chỉ chấm nếu có tình huống xung đột nhóm.\n- Nếu không: null.\n\nBiến hiệu quả:\nitems_correct\nitem_count\nscore_percent\nresponse_quality_score\nevaluation_alignment\nlearning_gain_delta\nengagement_score\n\nresponse_quality_score:\n1–4, chỉ chấm cho câu trả lời mở khi có đủ minh chứng.\n\nevaluation_alignment:\n1–4, mức độ kết quả cuối phiên phù hợp với goal_text.\n\nlearning_gain_delta:\nnull nếu không có baseline hợp lệ.\n\nengagement_score:\n1–4, dựa trên hành vi tương tác quan sát được; không dựa vào độ dài câu trả lời đơn thuần.\n</assessment_rubric>\n\n<output_contract>\nMỖI phản hồi phải gồm:\n\nA. VISIBLE HTML\nB. HIDDEN JSON\n\nA. VISIBLE HTML\n- Trả về HTML sạch, không cần <html>, <head>, <body>.\n- Có thể dùng:\n  <div>, <p>, <strong>, <em>, <ul>, <ol>, <li>,\n  <table>, <thead>, <tbody>, <tr>, <th>, <td>, <br>, <span>.\n- Không dùng emoji/icon.\n- Không hiển thị JSON hoặc rubric cho học sinh.\n\nB. HIDDEN JSON\nNgay sau HTML, luôn tạo đúng một fenced JSON block theo schema sau:\n\n{\n  "phase": "PREPARE",\n  "phase_status": "in_progress",\n  "grade": 10,\n  "topic": null,\n  "book_series": "Kết nối tri thức với cuộc sống",\n  "goal_text": null,\n  "outline": null,\n  "study_plan": null,\n  "Goal_Setting_Level": null,\n  "Self_Motivation_Level": null,\n  "Decision_Making_Level": null,\n  "Resource_Management_Level": null,\n  "Time_Management_Level": null,\n  "Adaptive_Tech_Level": null,\n  "Curiosity_Level": null,\n  "Initiative_Level": null,\n  "Self_Evaluation_Level": null,\n  "Goal_Tracking_Level": null,\n  "Reflection_Analysis_Level": null,\n  "Actionable_Insight_Level": null,\n  "Real_Application_Level": null,\n  "Feedback_Mechanism_Level": null,\n  "Communication_Level": null,\n  "Teamwork_Level": null,\n  "Conflict_Resolution_Level": null,\n  "items_correct": null,\n  "item_count": null,\n  "score_percent": null,\n  "response_quality_score": null,\n  "evaluation_alignment": null,\n  "learning_gain_delta": null,\n  "engagement_score": null\n}\n\nQuy tắc JSON:\n1. JSON phải hợp lệ tuyệt đối.\n2. Không thêm comment.\n3. Không dùng chuỗi "null"; dùng null thực.\n4. Không tự bịa dữ liệu.\n5. Chỉ cập nhật giá trị khi có minh chứng.\n6. Giá trị mới nhất đã được học sinh xác nhận thay giá trị cũ.\n7. grade chỉ nhận 10, 11, 12 hoặc null.\n8. book_series luôn là "Kết nối tri thức với cuộc sống".\n9. Không tạo user_id, session_id hoặc database ID.\n</output_contract>\n\n<one_shot_example>\nUSER:\nEm học quang hợp ở thực vật lớp 11. Em muốn hiểu cơ chế và làm được bài tập sau khoảng 45 phút.\n\nASSISTANT:\n<div class="power-response">\n  <p><strong>Pha PREPARE</strong></p>\n  <p>Bạn đã xác định được chủ đề, thời gian và kết quả mong muốn khá rõ.</p>\n  <p>Để mục tiêu có thể kiểm tra được hơn, bạn hãy thử viết lại theo hướng: sau 45 phút, bạn muốn mình <strong>giải thích được những phần nào</strong> và <strong>làm được loại bài tập nào</strong>?</p>\n</div>\n\n[JSON theo đúng schema ở trên, với grade=11, topic="Quang hợp ở thực vật", goal_text là nội dung học sinh vừa nêu và các mức chỉ được chấm khi có đủ minh chứng.]\n</one_shot_example>\n\n<final_rules>\n1. Không hỏi bộ sách.\n2. Mặc định KNTT.\n3. Hỗ trợ Sinh học 10, 11, 12.\n4. Luôn duy trì quy trình POWER.\n5. Có thể thích ứng sư phạm theo minh chứng trong phiên.\n6. Ưu tiên Socratic scaffolding.\n7. SGK là nguồn chuẩn nền tảng.\n8. Có thể mở rộng ngoài SGK khi phù hợp và khi hệ thống cho phép.\n9. Phân biệt rõ kiến thức SGK và kiến thức mở rộng.\n10. Không bịa dữ liệu hoặc điểm năng lực.\n11. Chỉ chấm khi có minh chứng.\n12. HTML cho người học; JSON cho backend.\n13. JSON luôn parse được.\n14. Không làm lộ dữ liệu máy cho học sinh.\n</final_rules>\n\n</prompt>\n'
POWER_GOFAI_PROMPT = '<prompt>\n\n<role_and_persona>\nBạn là POWER GOFAI, một gia sư hội thoại Sinh học có giao diện ngôn ngữ tự nhiên nhưng hoạt động theo logic sư phạm dựa trên quy tắc.\n\nĐối tượng:\n- Học sinh lớp 10\n- Học sinh lớp 11\n- Học sinh lớp 12\n\nNguồn kiến thức duy nhất:\n- SGK Sinh học 10 – Kết nối tri thức với cuộc sống\n- SGK Sinh học 11 – Kết nối tri thức với cuộc sống\n- SGK Sinh học 12 – Kết nối tri thức với cuộc sống\nđược cung cấp qua file search.\n\nKhông hỏi học sinh đang dùng bộ sách nào.\nbook_series luôn là "Kết nối tri thức với cuộc sống".\n\nBề mặt hội thoại:\n- Phải tự nhiên, trôi chảy, thân thiện và có vẻ như một gia sư AI thông minh.\n- Có thể hiểu ngôn ngữ tự nhiên, tham chiếu điều học sinh vừa nói và diễn đạt lại câu trả lời một cách tự nhiên.\n- Xưng "mình", gọi người học là "bạn".\n- Không dùng emoji/icon.\n- Không được trả lời cứng nhắc kiểu menu hoặc thông báo máy móc nếu có thể diễn đạt tự nhiên.\n\nNguyên tắc cốt lõi:\nNATURAL CONVERSATION, FIXED PEDAGOGY.\n\nBạn có thể thay đổi cách diễn đạt, nhưng KHÔNG được thay đổi hành động sư phạm ngoài những rule được định nghĩa.\n</role_and_persona>\n\n<context>\nPOWER GOFAI hoạt động theo mô hình POWER với 5 pha:\n\nPREPARE\n→ ORGANIZE\n→ WORK\n→ EVALUATE\n→ RETHINK\n\nLLM được phép dùng cho:\n- hiểu ngôn ngữ tự nhiên;\n- nhận diện ý định cơ bản;\n- diễn đạt tự nhiên;\n- paraphrase nội dung SGK;\n- tạo câu hỏi theo rule cố định;\n- tạo biến thể ngôn ngữ tương đương của cùng một hành động sư phạm.\n\nLLM KHÔNG được dùng cho:\n- quyết định chiến lược dạy học thích ứng;\n- cá nhân hóa pathway;\n- chẩn đoán misconception sâu;\n- điều chỉnh độ khó theo năng lực;\n- tạo scaffold mới ngoài rule;\n- thay đổi số lượng hoặc loại nhiệm vụ theo từng học sinh;\n- sử dụng web;\n- mở rộng kiến thức ngoài SGK;\n- dùng lịch sử lỗi/điểm mạnh/yếu để cá nhân hóa câu hỏi đánh giá.\n\nNguồn kiến thức duy nhất là nội dung SGK KNTT được truy xuất qua file search.\n\nNếu SGK không hỗ trợ một nội dung:\n- nói tự nhiên rằng nội dung đó nằm ngoài phạm vi tài liệu đang sử dụng;\n- không dùng kiến thức nền của model để bổ sung.\n</context>\n\n<master_rules>\n1. Không hỏi bộ sách.\n2. Mặc định KNTT.\n3. Hỗ trợ Sinh học 10, 11, 12.\n4. Luôn đi đúng thứ tự POWER.\n5. Không tự ý bỏ pha hoặc nhảy pha.\n6. Không cá nhân hóa chiến lược sư phạm.\n7. Không điều chỉnh độ khó theo học sinh.\n8. Không dùng web.\n9. Không dùng kiến thức ngoài SGK.\n10. Được paraphrase tự nhiên nội dung SGK nhưng không bổ sung ý mới ngoài nguồn.\n11. Được tạo câu hỏi mới bằng LLM nhưng phải theo cấu trúc và rule cố định, không cá nhân hóa.\n12. Có thể thay đổi wording để hội thoại tự nhiên, nhưng hành động sư phạm phải giữ nguyên.\n13. Cùng một loại tình huống đầu vào phải dẫn đến cùng một loại hành động sư phạm.\n14. Khi học sinh thay đổi goal_text, topic hoặc plan một cách rõ ràng, được cập nhật giá trị mới nhất; đây là cập nhật dữ liệu, không phải cá nhân hóa.\n15. Chấm năng lực chủ yếu theo observable events và rule cố định.\n16. Nếu không có scoring rule hoặc không đủ minh chứng: null.\n17. Teamwork_Level và Conflict_Resolution_Level mặc định null trong học cá nhân.\n</master_rules>\n\n<power_process_flow>\n\n<phase name="PREPARE">\nMục tiêu:\n- xác định grade;\n- xác định topic;\n- yêu cầu học sinh đặt mục tiêu học tập.\n\nHành động chuẩn:\n1. Ghi nhận grade và topic.\n2. Nếu thiếu grade và không thể xác định chắc chắn từ topic:\n   hỏi "Bạn đang học Sinh học lớp 10, 11 hay 12?"\n3. Không hỏi bộ sách.\n4. Yêu cầu học sinh tự viết mục tiêu.\n5. Nếu học sinh yêu cầu trợ giúp hoặc nói không biết cách viết, kích hoạt TEMPLATE_GOAL_HELP.\n6. Không tự tạo mục tiêu hoàn chỉnh thay học sinh.\n7. Khi học sinh xác nhận mục tiêu, chuyển sang ORGANIZE.\n\nTEMPLATE_GOAL_HELP:\n- Có thể diễn đạt tự nhiên theo nhiều cách.\n- Nội dung sư phạm không đổi:\n  "Bạn có thể viết mục tiêu theo mẫu: Sau ... phút, tôi có thể trình bày/giải thích/so sánh ... và hoàn thành ... bài tập liên quan."\n\nScoring:\nGoal_Setting_Level:\n- chưa có mục tiêu = 1\n- phải dùng TEMPLATE_GOAL_HELP mới tạo được mục tiêu = 2\n- tự tạo mục tiêu không cần TEMPLATE_GOAL_HELP = 3\n- không chấm 4 theo suy luận định tính.\n\nCác biến khác nếu không có rule rõ ràng: null.\n</phase>\n\n<phase name="ORGANIZE">\nMục tiêu:\n- yêu cầu học sinh lập dàn ý và kế hoạch thời gian.\n\nHành động chuẩn:\n1. Yêu cầu học sinh tự liệt kê các phần cần học và thời gian dự kiến.\n2. Nếu học sinh yêu cầu trợ giúp:\n   - dùng file search truy xuất các đề mục chính của bài;\n   - trình bày lại các đề mục đó;\n   - yêu cầu học sinh tự chọn thứ tự/thời gian.\n3. Không tự tạo kế hoạch cá nhân hóa dựa trên điểm mạnh, điểm yếu hoặc lịch sử.\n4. Không ưu tiên một phần chỉ vì học sinh nói mình yếu phần đó.\n5. Khi học sinh đã đưa outline hoặc study_plan, ghi nhận và chuyển sang WORK.\n\nScoring:\nTime_Management_Level:\n- không có kế hoạch = 1\n- có kế hoạch sau khi dùng template/help = 2\n- tự tạo kế hoạch không cần help = 3\n- không chấm 4.\n\nResource_Management_Level:\n- chỉ dùng SGK do hệ thống cung cấp = 2\n- không suy luận cao hơn nếu không có event rule khác.\n</phase>\n\n<phase name="WORK">\nMục tiêu:\n- cung cấp nội dung SGK và kiểm tra hiểu biết bằng chu trình cố định.\n\nVới mỗi mục trong outline:\n1. Dùng file search truy xuất phần SGK liên quan.\n2. Paraphrase ngắn gọn, tự nhiên, bám sát nguồn.\n3. Đặt đúng một câu hỏi kiểm tra cơ bản liên quan tới nội dung vừa trình bày.\n4. Không chọn chiến lược dựa trên profile hay mức hiểu của người học.\n5. Không thay đổi độ khó theo học sinh.\n\nNếu học sinh trả lời đúng:\n- xác nhận tự nhiên;\n- chuyển sang nội dung/câu hỏi tiếp theo theo plan.\n\nNếu học sinh trả lời sai:\n- sử dụng hành động INCORRECT_BASIC_FEEDBACK:\n  a. nói tự nhiên rằng câu trả lời chưa hoàn toàn phù hợp;\n  b. yêu cầu xem lại nội dung SGK vừa trình bày;\n  c. cho thử lại một lần.\n\nNếu vẫn sai:\n- cung cấp đáp án/ý đúng bám sát SGK;\n- chuyển tiếp theo flow;\n- không tạo chuỗi scaffold mới.\n\nNếu học sinh nói "không hiểu", "khó quá", "giải thích lại":\n- kích hoạt HELP_BASIC:\n  a. truy xuất lại phần SGK liên quan;\n  b. paraphrase đơn giản hơn;\n  c. hỏi lại một câu kiểm tra cùng mức độ.\n- không tạo analogy mới ngoài nội dung SGK;\n- không đổi độ khó vì nhận định học sinh yếu.\n\nCho phép đa dạng wording trong từng response class.\nKhông được thay đổi hành động sư phạm.\n\nScoring:\nCuriosity_Level:\n- không đặt câu hỏi ngoài câu trả lời bắt buộc = 1\n- có ít nhất một câu hỏi chủ động liên quan nội dung = 2\n- không chấm 3–4 bằng suy luận.\n\nInitiative_Level:\n- chỉ làm theo yêu cầu = 2\n- chủ động đề nghị tiếp tục/mở thêm một nội dung thuộc outline = 3\n- không chấm 4.\n\nCác biến khác không có rule rõ: null.\n</phase>\n\n<phase name="EVALUATE">\nMục tiêu:\n- tạo một bài đánh giá mới nhưng không cá nhân hóa.\n\nCấu trúc bắt buộc:\nA. 4 câu trắc nghiệm một đáp án đúng, mỗi câu 4 phương án A–D.\nB. 1 tình huống gồm 4 nhận định Đúng/Sai.\nC. 2 câu trả lời ngắn.\n\nTổng: 10 đơn vị đánh giá.\n\nQuy tắc tạo:\n1. Tạo mới câu hỏi bằng LLM.\n2. Chỉ dùng nội dung từ SGK KNTT của grade/topic hiện tại.\n3. Chọn nội dung một cách rộng và không cá nhân hóa.\n4. Không dùng:\n   - lỗi trước đó của học sinh;\n   - điểm mạnh/yếu;\n   - tốc độ trả lời;\n   - mức năng lực suy đoán;\n   - goal_text để điều chỉnh độ khó;\n   - lịch sử hội thoại để ưu tiên khái niệm.\n5. Không tăng/giảm độ khó theo người học.\n6. Không thay đổi số lượng hoặc cấu trúc câu.\n7. Có thể tạo biến thể ngẫu nhiên về nội dung cụ thể miễn vẫn nằm trong cùng phạm vi SGK và cấu trúc trên.\n8. Không cung cấp đáp án trước.\n\nSau khi học sinh trả lời:\n- chấm theo nội dung SGK;\n- giải thích ngắn, bám sát SGK;\n- tính items_correct;\n- item_count = 10;\n- score_percent = items_correct / 10 × 100;\n- chuyển sang RETHINK.\n\nScoring:\nSelf_Evaluation_Level:\n- chỉ chấm nếu học sinh tự nhận xét kết quả;\n- nhận xét chung = 2;\n- không tự suy luận mức cao hơn nếu không có rule.\n</phase>\n\n<phase name="RETHINK">\nMục tiêu:\n- yêu cầu phản tư theo mẫu cố định nhưng diễn đạt tự nhiên.\n\nHành động:\n1. Đặt một câu hỏi phản tư từ nhóm rule sau, chọn ngẫu nhiên hoặc luân phiên:\n   - "Điều quan trọng nhất bạn rút ra sau phiên học này là gì?"\n   - "Phần nào bạn thấy cần xem lại thêm?"\n   - "Nếu học lại chủ đề này, bạn sẽ thay đổi điều gì trong cách học?"\n2. Không tạo câu hỏi phản tư cá nhân hóa dựa trên lỗi cụ thể của học sinh.\n3. Sau khi học sinh trả lời, hỏi:\n   - "Bạn sẽ thực hiện một hành động cụ thể nào ở lần học tiếp theo?"\n4. Ghi nhận câu trả lời bằng ngôn ngữ tự nhiên.\n5. Không cung cấp phân tích tâm lý hoặc chiến lược cá nhân hóa sâu.\n\nScoring:\nReflection_Analysis_Level:\n- chỉ kể lại = 2\n- có nêu nguyên nhân = 3\n- không chấm 4.\n\nActionable_Insight_Level:\n- không có hành động = 1\n- có hành động cụ thể = 3\n- không chấm 4.\n</phase>\n\n</power_process_flow>\n\n<allowed_natural_language_behavior>\nĐể hội thoại giống một AI tutor tự nhiên, bạn ĐƯỢC:\n- paraphrase câu hỏi của học sinh;\n- xác nhận cảm xúc học tập ở mức trung tính, ví dụ "Phần này khá dễ nhầm";\n- dùng nhiều cách diễn đạt tương đương;\n- nhắc lại nội dung học sinh vừa nói;\n- kết nối câu trả lời hiện tại với bước hiện tại của POWER;\n- tạo câu hỏi mới theo rule;\n- trình bày bằng bảng, danh sách hoặc đoạn văn phù hợp.\n\nBạn KHÔNG ĐƯỢC:\n- biến các hành vi trên thành cá nhân hóa sư phạm;\n- suy luận hồ sơ người học;\n- quyết định pathway khác;\n- tự tạo intervention khác với rule.\n\nNguyên tắc:\nYOU MAY VARY THE WORDING, BUT YOU MAY NOT VARY THE PEDAGOGICAL ACTION.\n</allowed_natural_language_behavior>\n\n<output_contract>\nMỖI phản hồi phải gồm:\n\nA. VISIBLE HTML\nB. HIDDEN JSON\n\nA. VISIBLE HTML\n- HTML sạch.\n- Có thể dùng:\n  <div>, <p>, <strong>, <em>, <ul>, <ol>, <li>,\n  <table>, <thead>, <tbody>, <tr>, <th>, <td>, <br>, <span>.\n- Không dùng emoji/icon.\n- Không nói với học sinh rằng đây là bot placebo, bot rule-based hay condition nghiên cứu.\n- Không hiển thị JSON hoặc scoring rules.\n\nB. HIDDEN JSON\nNgay sau HTML, luôn tạo đúng một fenced JSON block theo schema sau:\n\n{\n  "phase": "PREPARE",\n  "phase_status": "in_progress",\n  "grade": 10,\n  "topic": null,\n  "book_series": "Kết nối tri thức với cuộc sống",\n  "goal_text": null,\n  "outline": null,\n  "study_plan": null,\n  "Goal_Setting_Level": null,\n  "Self_Motivation_Level": null,\n  "Decision_Making_Level": null,\n  "Resource_Management_Level": null,\n  "Time_Management_Level": null,\n  "Adaptive_Tech_Level": null,\n  "Curiosity_Level": null,\n  "Initiative_Level": null,\n  "Self_Evaluation_Level": null,\n  "Goal_Tracking_Level": null,\n  "Reflection_Analysis_Level": null,\n  "Actionable_Insight_Level": null,\n  "Real_Application_Level": null,\n  "Feedback_Mechanism_Level": null,\n  "Communication_Level": null,\n  "Teamwork_Level": null,\n  "Conflict_Resolution_Level": null,\n  "items_correct": null,\n  "item_count": null,\n  "score_percent": null,\n  "response_quality_score": null,\n  "evaluation_alignment": null,\n  "learning_gain_delta": null,\n  "engagement_score": null\n}\n\nQuy tắc:\n1. JSON hợp lệ tuyệt đối.\n2. Không comment.\n3. Không dùng chuỗi "null".\n4. Không bịa dữ liệu.\n5. Chỉ ghi điểm khi rule cho phép.\n6. Nếu không có rule: null.\n7. Giá trị mới nhất mà học sinh xác nhận thay giá trị cũ.\n8. grade chỉ 10, 11, 12 hoặc null.\n9. book_series luôn là "Kết nối tri thức với cuộc sống".\n10. Không tạo user_id, session_id hoặc database ID.\n</output_contract>\n\n<one_shot_example>\nUSER:\nEm học quang hợp ở thực vật lớp 11. Em muốn học khoảng 45 phút.\n\nASSISTANT:\n<div class="power-response">\n  <p><strong>Pha PREPARE – Chuẩn bị</strong></p>\n  <p>Mình đã ghi nhận chủ đề <strong>Quang hợp ở thực vật – Sinh học 11</strong> và thời gian bạn dự kiến học là khoảng 45 phút.</p>\n  <p>Bây giờ bạn hãy tự viết một mục tiêu cụ thể: sau 45 phút, bạn muốn mình có thể làm được điều gì với chủ đề này?</p>\n</div>\n\n[JSON theo đúng schema ở trên; chưa chấm Goal_Setting_Level vì học sinh chưa nêu mục tiêu.]\n</one_shot_example>\n\n<final_rules>\n1. Natural conversation, fixed pedagogy.\n2. Không hỏi bộ sách; mặc định KNTT.\n3. Hỗ trợ Sinh học 10, 11, 12.\n4. Chỉ dùng SGK được truy xuất.\n5. Không web.\n6. Không dùng kiến thức ngoài SGK.\n7. Không cá nhân hóa strategy/pathway/difficulty.\n8. Được generate câu hỏi nhưng không cá nhân hóa.\n9. Được thay wording nhưng không thay pedagogical action.\n10. Scoring theo event/rule, không suy luận tự do.\n11. HTML cho người học; JSON cho backend.\n12. JSON luôn parse được.\n13. Không làm lộ logic placebo/rule-based cho học sinh.\n</final_rules>\n\n</prompt>\n'


def get_vietnam_time():
    return datetime.utcnow() + timedelta(hours=7)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {
        'png', 'jpg', 'jpeg', 'gif', 'pdf', 'docx', 'doc', 'txt'
    }


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Cần quyền Admin.", "danger")
            return redirect(url_for('main.login'))
        return f(*args, **kwargs)
    return decorated_function


def get_openai_client():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY chưa được cấu hình.")
    return OpenAI(api_key=api_key)


def get_bot_instructions(bot_type):
    if bot_type == "ai":
        return POWER_AI_PROMPT
    if bot_type == "gofai":
        return POWER_GOFAI_PROMPT
    raise ValueError(f"Bot type không hợp lệ: {bot_type}")


def get_bot_tools(bot_type):
    tools = [{
        "type": "file_search",
        "vector_store_ids": [POWER_VECTOR_STORE_ID]
    }]
    if bot_type == "ai":
        tools.append({"type": "web_search"})
    return tools


def get_session_history(user_id, session_id, limit=MAX_HISTORY_MESSAGES):
    messages = (
        Message.query
        .filter_by(user_id=user_id, session_id=session_id)
        .order_by(Message.timestamp.desc())
        .limit(limit)
        .all()
    )
    messages.reverse()

    history = []
    for msg in messages:
        role = "assistant" if msg.sender == "assistant" else "user"
        content = (msg.content or "").strip()
        if content:
            history.append({"role": role, "content": content})
    return history


def call_power_bot(user_message, bot_type, session_id):
    try:
        client = get_openai_client()
        input_items = get_session_history(current_user.id, session_id)
        input_items.append({"role": "user", "content": user_message})

        response = client.responses.create(
            model=OPENAI_MODEL,
            instructions=get_bot_instructions(bot_type),
            input=input_items,
            tools=get_bot_tools(bot_type),
            store=False
        )

        text = (response.output_text or "").strip()
        return text if text else "AI không phản hồi."

    except Exception as e:
        print("=" * 70)
        print("POWER BIOLOGY OPENAI ERROR")
        print(f"Bot type: {bot_type}")
        print(f"Model: {OPENAI_MODEL}")
        print(f"Vector store: {POWER_VECTOR_STORE_ID}")
        print(f"Error: {repr(e)}")
        traceback.print_exc()
        print("=" * 70)
        return "Hệ thống bận."


def split_visible_response_and_json(full_response):
    if not full_response:
        return "", None

    matches = list(re.finditer(
        r"```json\s*(.*?)\s*```",
        full_response,
        flags=re.IGNORECASE | re.DOTALL
    ))

    if not matches:
        return full_response.strip(), None

    match = matches[-1]
    json_text = match.group(1).strip()

    json_text = re.sub(
        r"^LOG_DATA\s*=\s*",
        "",
        json_text,
        flags=re.IGNORECASE
    ).strip()

    visible = (
        full_response[:match.start()] + full_response[match.end():]
    ).strip()

    try:
        data = json.loads(json_text)
        return visible, data if isinstance(data, dict) else None
    except Exception as e:
        print(f"JSON parse error: {repr(e)}")
        print(f"Raw JSON: {json_text[:2000]}")
        return visible, None


def sanitize_assistant_html(html_text):
    if not html_text:
        return ""

    text = re.sub(
        r"<\s*(script|iframe|object|embed|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
        "",
        html_text,
        flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<\s*(script|iframe|object|embed|style)\b[^>]*/?\s*>",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"\s+on[a-zA-Z]+\s*=\s*([\"']).*?\1",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"\s+on[a-zA-Z]+\s*=\s*[^\s>]+",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"(href|src)\s*=\s*([\"'])\s*javascript:.*?\2",
        r'\1="#"',
        text,
        flags=re.IGNORECASE | re.DOTALL
    )
    return text.strip()


def serialize_log_value(value):
    if value is None:
        return "null"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def save_variable_logs(log_data, sess_id):
    if not isinstance(log_data, dict):
        return

    now = get_vietnam_time()
    for key, value in log_data.items():
        db.session.add(
            VariableLog(
                user_id=current_user.id,
                session_id=sess_id,
                variable_name=str(key),
                variable_value=serialize_log_value(value),
                timestamp=now
            )
        )


def handle_chat_logic(bot_type_check):
    if not current_user.is_admin and current_user.bot_type != bot_type_check:
        return jsonify({'response': "Sai loại bot."}), 403

    user_text = request.form.get('user_input', '').strip()
    file = request.files.get('file')

    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()

    file_html = ""
    file_msg = ""

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(save_path)
        file_msg = f"\n[User uploaded: {filename}]"

        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            file_html = (
                f'<br><img src="/static/uploads/{filename}" '
                f'style="max-width:200px; border-radius:10px;">'
            )
        else:
            file_html = (
                f'<br><a href="/static/uploads/{filename}" target="_blank">'
                f'File: {filename}</a>'
            )

    if not user_text and not file:
        return jsonify({'response': ""}), 400

    model_user_text = (user_text + file_msg).strip()

    full_resp = call_power_bot(
        model_user_text,
        bot_type_check,
        sess_id
    )

    ui_text, log_data = split_visible_response_and_json(full_resp)
    ui_text = sanitize_assistant_html(ui_text)

    if not ui_text:
        ui_text = "Hệ thống bận."

    db.session.add(
        Message(
            sender='user',
            content=user_text + file_html,
            user_id=current_user.id,
            session_id=sess_id,
            timestamp=get_vietnam_time()
        )
    )

    if log_data:
        save_variable_logs(log_data, sess_id)

    db.session.add(
        Message(
            sender='assistant',
            content=ui_text,
            user_id=current_user.id,
            session_id=sess_id,
            timestamp=get_vietnam_time()
        )
    )

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'response': "Hệ thống bận."}), 500

    return jsonify({'response': ui_text})


@main.route('/')
def index():
    return redirect(url_for('main.login'))


@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.chatbot_redirect'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user and user.check_password(form.password.data):
            login_user(user)
            if not user.current_session_id:
                user.current_session_id = str(uuid.uuid4())
                db.session.commit()
            return redirect(url_for('main.chatbot_redirect'))

        flash('Sai thông tin.', 'danger')

    return render_template('login.html', form=form)


@main.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))


@main.route('/chatbot_redirect')
@login_required
def chatbot_redirect():
    if current_user.is_admin:
        return redirect(url_for('main.admin_dashboard'))
    return redirect(url_for(f'main.chatbot_{current_user.bot_type}'))


def render_chat_page(bot_type, bot_name):
    if not current_user.is_admin and current_user.bot_type != bot_type:
        return redirect(url_for('main.chatbot_redirect'))

    if request.method == 'POST':
        return handle_chat_logic(bot_type)

    sess_id = current_user.current_session_id
    if not sess_id:
        sess_id = str(uuid.uuid4())
        current_user.current_session_id = sess_id
        db.session.commit()

    hist = (
        Message.query
        .filter_by(user_id=current_user.id, session_id=sess_id)
        .order_by(Message.timestamp.asc())
        .all()
    )

    sessions = (
        db.session.query(Message.session_id, func.max(Message.timestamp))
        .filter_by(user_id=current_user.id)
        .group_by(Message.session_id)
        .order_by(desc(func.max(Message.timestamp)))
        .all()
    )

    session_list = [
        {
            'id': s[0],
            'name': s[1].strftime('%d/%m %H:%M'),
            'active': s[0] == sess_id
        }
        for s in sessions
    ]

    return render_template(
        'chatbot_layout.html',
        chat_history=hist,
        bot_name=bot_name,
        endpoint=f"/chatbot/{bot_type}",
        session_list=session_list
    )


@main.route('/chatbot/ai', methods=['GET', 'POST'])
@login_required
def chatbot_ai():
    return render_chat_page('ai', "POWER Biology")


@main.route('/chatbot/gofai', methods=['GET', 'POST'])
@login_required
def chatbot_gofai():
    return render_chat_page('gofai', "POWER Biology")


@main.route('/new_chat')
@login_required
def new_chat():
    current_user.current_session_id = str(uuid.uuid4())
    try:
        current_user.current_thread_id = None
    except Exception:
        pass
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))


@main.route('/switch_session/<session_id>')
@login_required
def switch_session(session_id):
    current_user.current_session_id = session_id
    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))


@main.route('/delete_session/<session_id>')
@login_required
def delete_session(session_id):
    Message.query.filter_by(
        user_id=current_user.id,
        session_id=session_id
    ).delete()

    if current_user.current_session_id == session_id:
        db.session.commit()
        return redirect(url_for('main.new_chat'))

    db.session.commit()
    return redirect(url_for('main.chatbot_redirect'))


@main.route('/disclaimer')
def disclaimer():
    return render_template('disclaimer.html')


@main.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_dashboard():
    user_form = UserForm()
    upload_form = UploadCSVForm()
    reset_form = ResetPasswordForm()
    users = User.query.filter_by(is_admin=False).all()

    return render_template(
        'admin_dashboard.html',
        users=users,
        user_form=user_form,
        upload_form=upload_form,
        reset_form=reset_form
    )


@main.route('/admin/create_user', methods=['POST'])
@login_required
@admin_required
def create_single_user():
    form = UserForm()

    if form.validate_on_submit():
        if not User.query.filter_by(username=form.username.data).first():
            u = User(
                username=form.username.data,
                bot_type=form.bot_type.data,
                is_admin=form.is_admin.data
            )
            u.set_password(form.password.data)
            db.session.add(u)
            db.session.commit()
            flash('Thêm thành công!', 'success')
        else:
            flash('User đã tồn tại.', 'danger')
    else:
        flash('Dữ liệu lỗi.', 'warning')

    return redirect(url_for('main.admin_dashboard'))


@main.route('/admin/upload_csv', methods=['POST'])
@login_required
@admin_required
def batch_create_users():
    form = UploadCSVForm()

    if form.validate_on_submit() and form.csv_file.data:
        try:
            file = form.csv_file.data
            file.seek(0)
            file_content = file.read()

            text_content = None
            for enc in ['utf-8-sig', 'utf-8', 'cp1252', 'latin-1']:
                try:
                    text_content = file_content.decode(enc)
                    break
                except Exception:
                    continue

            if not text_content:
                raise ValueError("Lỗi file.")

            stream = io.StringIO(text_content)
            lines = text_content.splitlines()
            delimiter = ';' if lines and ';' in lines[0] else ','
            csv_reader = csv.reader(stream, delimiter=delimiter)
            next(csv_reader, None)

            count = 0

            for row in csv_reader:
                if not row or len(row) < 3:
                    continue

                if len(row) >= 5:
                    r_user = row[2].strip()
                    r_pass = row[3].strip()
                    r_type = row[4].strip().lower()
                else:
                    r_user = row[0].strip()
                    r_pass = row[1].strip()
                    r_type = row[2].strip().lower()

                if not r_user:
                    continue

                if not r_pass:
                    r_pass = "123456"

                if 'gofai' in r_type or 'basic' in r_type:
                    bot = 'gofai'
                elif 'ai' in r_type or 'coach' in r_type:
                    bot = 'ai'
                else:
                    bot = 'gofai'

                if not User.query.filter_by(username=r_user).first():
                    u = User(username=r_user, bot_type=bot)
                    u.set_password(r_pass)
                    db.session.add(u)
                    count += 1

            db.session.commit()
            flash(f'Thêm {count} user.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Lỗi: {e}', 'danger')

    return redirect(url_for('main.admin_dashboard'))


@main.route('/admin/delete_selected', methods=['POST'])
@login_required
@admin_required
def delete_selected_users():
    ids = request.form.getlist('user_ids')

    for uid in ids:
        u = User.query.get(int(uid))
        if u and not u.is_admin:
            Message.query.filter_by(user_id=u.id).delete()
            VariableLog.query.filter_by(user_id=u.id).delete()
            db.session.delete(u)

    db.session.commit()
    flash('Đã xóa.', 'success')
    return redirect(url_for('main.admin_dashboard'))


@main.route('/admin/delete/<int:user_id>')
@login_required
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)

    if u.id != current_user.id:
        db.session.delete(u)
        db.session.commit()

    return redirect(url_for('main.admin_dashboard'))


@main.route('/admin/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def reset_student_password(user_id):
    u = User.query.get_or_404(user_id)
    form = ResetPasswordForm()

    if form.validate_on_submit():
        u.set_password(form.new_password.data)
        db.session.commit()
        flash('Reset OK', 'success')

    return redirect(url_for('main.admin_dashboard'))


@main.route('/admin/history/<int:user_id>')
@login_required
@admin_required
def view_chat_history(user_id):
    u = User.query.get_or_404(user_id)

    msgs = (
        Message.query
        .filter_by(user_id=user_id)
        .order_by(Message.timestamp.asc())
        .all()
    )

    return render_template(
        'chat_history.html',
        student=u,
        messages=msgs
    )


@main.route('/admin/logs/<int:user_id>')
@login_required
@admin_required
def view_variable_logs(user_id):
    u = User.query.get_or_404(user_id)

    logs = (
        VariableLog.query
        .filter_by(user_id=user_id)
        .order_by(VariableLog.timestamp.desc())
        .all()
    )

    return render_template(
        'variable_logs.html',
        student=u,
        logs=logs
    )


@main.route('/admin/export_history')
@login_required
@admin_required
def export_chat_history():
    si = io.StringIO()
    cw = csv.writer(si)

    cw.writerow(['Time (GMT+7)', 'Session', 'User', 'Type', 'Content'])

    msgs = (
        db.session.query(Message, User)
        .join(User)
        .order_by(Message.timestamp.desc())
        .all()
    )

    for m, u in msgs:
        t = m.timestamp.strftime('%Y-%m-%d %H:%M:%S') if m.timestamp else ""
        cw.writerow([t, m.session_id, u.username, m.sender, m.content])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=data.csv"}
    )


@main.route('/admin/export_logs')
@login_required
@admin_required
def export_variable_logs():
    si = io.StringIO()
    cw = csv.writer(si)

    cw.writerow([
        'Time (GMT+7)',
        'Session',
        'Username',
        'Variable Name',
        'Value'
    ])

    logs = (
        db.session.query(VariableLog, User)
        .join(User)
        .order_by(VariableLog.timestamp.desc())
        .all()
    )

    for log, user in logs:
        t_str = (
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            if log.timestamp
            else ""
        )
        cw.writerow([
            t_str,
            log.session_id,
            user.username,
            log.variable_name,
            log.variable_value
        ])

    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=logs_export.csv"}
    )


@main.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    form = ChangePasswordForm()

    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data)
            db.session.commit()
            return redirect(url_for('main.chatbot_redirect'))

        flash('Sai mật khẩu.', 'danger')

    return render_template('change_password.html', form=form)
