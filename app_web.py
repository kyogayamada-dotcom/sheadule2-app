import streamlit as st
import pandas as pd
import datetime
import io
import re
import random
from collections import Counter

# ==========================================
# 1. カレンダー・ロジック設定
# ==========================================
def get_open_periods(date_obj):
    """日付ごとの開講コマ定義"""
    m, d = date_obj.month, date_obj.day

    # 1. 1月7, 8, 9日は 3,4,5,6講
    if m == 1 and d in [7, 8, 9]:
        return [3, 4, 5, 6]

    # 2. 12/23, 24は 3-6講
    if m == 12 and d in [23, 24]:
        return [3, 4, 5, 6]

    # 3. 特定の日付の1,2講をバツにする
    if (m == 12 and d in [20, 21, 27]) or (m == 1 and d in [4, 10, 11]):
        return [3, 4, 5]
    if (m == 12 and d in [25, 26]) or (m == 1 and d == 6):
        return [3, 4, 5, 6]
    if m == 12 and d == 28:
        return [3, 4]

    # 4. 通常ルール
    if (m == 12 and (2<=d<=5 or 9<=d<=12 or 16<=d<=19)) or \
       (m == 1 and (13<=d<=16 or 20<=d<=23 or 27<=d<=30)):
        return [4, 5, 6]
    
    if (m == 12 and d in [6, 13]) or (m == 1 and d in [17, 24, 31]):
        return [2, 3, 4, 5]

    return []

def parse_existing_excel(uploaded_file):
    """アップロードされたExcelから現在のschedule_mapを復元する"""
    try:
        df = pd.read_excel(uploaded_file, sheet_name="時間割", header=None)
        existing_map = {}
        col_date_map = {}
        
        for r_idx, row in df.iterrows():
            row_vals = row.values
            first_cell = str(row_vals[0])
            
            if "講" in first_cell:
                col_date_map = {}
                for c_idx, val in enumerate(row_vals):
                    val_str = str(val)
                    match = re.search(r"(\d+)/(\d+)", val_str)
                    if match:
                        m, d = int(match.group(1)), int(match.group(2))
                        y = 2025 if m == 12 else 2026
                        col_date_map[c_idx] = datetime.date(y, m, d)
            
            elif first_cell in ['1', '2', '3', '4', '5', '6']:
                p = int(first_cell)
                for c_idx, val in enumerate(row_vals):
                    if c_idx in col_date_map:
                        d_date = col_date_map[c_idx]
                        cell_text = str(val)
                        if cell_text and cell_text not in ["nan", "×", "-"]:
                            students = [s.strip() for s in cell_text.split('\n') if s.strip()]
                            existing_map[(d_date, p)] = students
                            
        return existing_map
    except Exception as e:
        st.error(f"Excel解析エラー: {e}")
        return {}

def apply_standard_schedule(target_weekly_data, standard_pattern):
    """
    標準パターンを全期間に適用する。
    ただし、12/24〜1/5 (冬期講習期間) は除外する。
    Noneの値は「変更なし」として扱う。
    """
    exclude_start = datetime.date(2025, 12, 24)
    exclude_end = datetime.date(2026, 1, 5)
    
    updated_data = {}
    
    for label, df in target_weekly_data.items():
        new_df = df.copy()
        for date_str in new_df.columns:
            match = re.search(r"(\d+)/(\d+)", date_str)
            if not match: continue
            m, d = int(match.group(1)), int(match.group(2))
            y = 2025 if m == 12 else 2026
            try:
                d_obj = datetime.date(y, m, d)
            except:
                continue
            
            # 冬期講習期間ならスキップ
            if exclude_start <= d_obj <= exclude_end:
                continue
                
            # 曜日の取得 (0=Mon, 5=Sat)
            wd = d_obj.weekday()
            if wd > 5: continue # 日曜はスキップ
            
            # 標準パターンの適用
            day_vals = standard_pattern.get(wd)
            if day_vals:
                for p in range(1, 7):
                    val = day_vals[p-1]
                    # Noneの場合は何もしない(既存の値を維持)
                    if val is not None:
                        new_df.loc[p, date_str] = val
        
        updated_data[label] = new_df
    return updated_data

# ==========================================
# 2. データ処理・計算ロジック
# ==========================================
def calculate_schedule(teacher_weekly_data, req_df, student_weekly_data, teacher_name, existing_schedule_map=None):
    
    # A. 先生シフト解析
    teacher_capacity = {}
    
    for week_label, df in teacher_weekly_data.items():
        for date_str in df.columns:
            match = re.search(r"(\d+)/(\d+)", date_str)
            if not match: continue
            m, d = int(match.group(1)), int(match.group(2))
            y = 2025 if m == 12 else 2026
            try: d_date = datetime.date(y, m, d)
            except: continue
            
            open_periods = get_open_periods(d_date)
            
            for p in range(1, 7):
                try: val = str(df.loc[p, date_str])
                except: continue
                
                if p not in open_periods: continue
                
                if any(x in val for x in ["〇", "○", "OK", "全"]):
                    teacher_capacity[(d_date, p)] = 2
                elif any(x in val for x in ["△", "▲", "半", "1"]):
                    teacher_capacity[(d_date, p)] = 1

    # 全スロット作成
    all_slots = []
    for (d, p), cap in teacher_capacity.items():
        all_slots.append((d, p, cap))
    
    # B. 生徒データ解析
    students = {}
    for _, row in req_df.iterrows():
        name = row['生徒名']
        reqs = {k: int(row.get(k, 0)) for k in ["国語", "数学", "英語", "理科", "社会"]}
        students[name] = {"reqs": reqs, "remaining": sum(reqs.values())}

    # C. 生徒シフト解析
    student_availability = {}
    for s_name, weekly_data in student_weekly_data.items():
        if not weekly_data: continue
        for week_label, df in weekly_data.items():
            for date_str in df.columns:
                match = re.search(r"(\d+)/(\d+)", date_str)
                if not match: continue
                m, d = int(match.group(1)), int(match.group(2))
                y = 2025 if m == 12 else 2026
                try: d_date = datetime.date(y, m, d)
                except: continue
                
                for p in range(1, 7):
                    try: val = str(df.loc[p, date_str])
                    except: continue
                    
                    if any(x in val for x in ["〇", "○", "OK", "△", "▲", "1", "2", "3", "全"]):
                        student_availability[(s_name, d_date, p)] = True
                    else:
                        student_availability[(s_name, d_date, p)] = False

    # D. 計算準備
    schedule_map = { (d, p): [] for d, p, cap in all_slots }
    date_counts = Counter()
    daily_student_counts = Counter()

    if existing_schedule_map:
        for (d, p), assigned_list in existing_schedule_map.items():
            if (d, p) in schedule_map:
                schedule_map[(d, p)] = assigned_list[:] 
                if len(assigned_list) > 0:
                    date_counts[d] += len(assigned_list)
                for entry in assigned_list:
                    s_name = entry.split("(")[0]
                    daily_student_counts[(s_name, d)] += 1

    random.seed(42)
    max_loops = 3000
    loop_count = 0

    while loop_count < max_loops:
        loop_count += 1
        assigned_in_this_loop = False
        
        def get_slot_priority(slot):
            d, p, cap = slot
            current_assigned = schedule_map[(d, p)]
            current_len = len(current_assigned)
            
            if current_len >= cap: return -99999
            
            score = 0
            if current_len == 1 and cap == 2:
                score += 5000 

            if len(schedule_map.get((d, p-1), [])) > 0: score += 100
            if len(schedule_map.get((d, p+1), [])) > 0: score += 100
            
            score += date_counts[d] * 10
            score += random.random()
            return score

        all_slots.sort(key=get_slot_priority, reverse=True)

        for d, p, cap in all_slots:
            current_assigned = schedule_map[(d, p)]
            if len(current_assigned) >= cap: continue

            candidates = []
            for s_name, data in students.items():
                if data["remaining"] <= 0: continue
                if daily_student_counts[(s_name, d)] >= 3: continue 
                if not student_availability.get((s_name, d, p), False): continue
                
                is_already_in = False
                for entry in current_assigned:
                    if entry.startswith(s_name + "("):
                        is_already_in = True; break
                if is_already_in: continue

                candidates.append(s_name)
            
            if not candidates: continue

            def get_student_priority(s_name):
                p_score = 0
                prev_users = schedule_map.get((d, p-1), [])
                for u in prev_users:
                    if u.startswith(s_name + "("): p_score += 20000
                
                next_users = schedule_map.get((d, p+1), [])
                for u in next_users:
                    if u.startswith(s_name + "("): p_score += 20000

                if daily_student_counts[(s_name, d)] > 0:
                    p_score += 500
                
                p_score += students[s_name]["remaining"] * 10
                p_score += random.random()
                return p_score

            candidates.sort(key=get_student_priority, reverse=True)
            
            s = candidates[0]
            items = sorted([(v, k) for k, v in students[s]["reqs"].items() if v > 0], reverse=True)
            if not items: continue
            subj = items[0][1]

            students[s]["reqs"][subj] -= 1
            students[s]["remaining"] -= 1
            daily_student_counts[(s, d)] += 1
            date_counts[d] += 1
            
            schedule_map[(d, p)].append(f"{s}({subj})")
            assigned_in_this_loop = True
            break
        
        if not assigned_in_this_loop: break

    all_dates = sorted(list(set([x[0] for x in all_slots])))
    unscheduled = []
    for s, data in students.items():
        for subj, cnt in data["reqs"].items():
            if cnt > 0: unscheduled.append({"生徒名": s, "科目": subj, "不足": cnt})
    
    return schedule_map, all_dates, unscheduled

# ==========================================
# 3. UIヘルパー関数
# ==========================================
def get_week_ranges():
    start_date = datetime.date(2025, 12, 1)
    end_date = datetime.date(2026, 1, 31)
    weeks = []
    current_dates = []
    curr = start_date
    while curr <= end_date:
        current_dates.append(curr)
        if len(current_dates) == 7 or curr == end_date:
            label = f"{current_dates[0].strftime('%m/%d')} 〜 {current_dates[-1].strftime('%m/%d')}"
            weeks.append({"label": label, "dates": current_dates})
            current_dates = []
        curr += datetime.timedelta(days=1)
    return weeks

def create_weekly_df(dates):
    col_names = [d.strftime("%m/%d(%a)") for d in dates]
    data = {}
    for d_obj, col in zip(dates, col_names):
        open_periods = get_open_periods(d_obj)
        col_data = []
        for p in range(1, 7):
            val = "〇" if p in open_periods else "×"
            col_data.append(val)
        data[col] = col_data
    return pd.DataFrame(data, index=[1, 2, 3, 4, 5, 6])

def create_student_req_df(student_names):
    data = []
    for name in student_names:
        data.append({"生徒名": name, "国語": 0, "数学": 0, "英語": 0, "理科": 0, "社会": 0})
    return pd.DataFrame(data)

# ==========================================
# 4. メインアプリ (Streamlit)
# ==========================================
st.set_page_config(page_title="時間割作成(スマホ完結)", layout="wide")
st.title("📱 個別指導塾 時間割作成 (改良版)")

# --- セッション状態の初期化 ---
weeks_info = get_week_ranges()

if "teacher_weekly_data" not in st.session_state: st.session_state.teacher_weekly_data = None
if "student_req_df" not in st.session_state: st.session_state.student_req_df = None
if "student_weekly_data" not in st.session_state: st.session_state.student_weekly_data = {}
if "student_list" not in st.session_state: st.session_state.student_list = []
if "existing_schedule_map" not in st.session_state: st.session_state.existing_schedule_map = None

# --- サイドバー ---
with st.sidebar:
    st.header("1. 設定モード")
    mode = st.radio("作成モードを選択", ["新規作成", "追加作成(更新)"])
    
    teacher_name = st.text_input("先生の名前", "佐藤")
    
    if mode == "追加作成(更新)":
        st.info("前回のExcelファイルをアップロードしてください。")
        uploaded_file = st.file_uploader("完成時間割Excel", type=["xlsx"])
        if uploaded_file:
            existing_map = parse_existing_excel(uploaded_file)
            if existing_map:
                st.session_state.existing_schedule_map = existing_map
                st.success(f"既存データを読み込みました: {len(existing_map)}コマ分")
            else:
                st.error("データの読み込みに失敗しました。")
        else:
            st.session_state.existing_schedule_map = None
    else:
        st.session_state.existing_schedule_map = None

    st.subheader("生徒リスト設定")
    
    default_students = "追加の生徒A\n追加の生徒B" if mode == "追加作成(更新)" else "山田くん\n田中さん\n高橋くん"
    s_input = st.text_area("名前を入力 (改行区切り)", default_students, height=100)
    
    if st.button("入力を開始/リセット"):
        new_list = [s.strip() for s in s_input.split('\n') if s.strip()]
        st.session_state.student_list = new_list
        
        t_data = {}
        for w in weeks_info: t_data[w["label"]] = create_weekly_df(w["dates"])
        st.session_state.teacher_weekly_data = t_data
        
        st.session_state.student_req_df = create_student_req_df(new_list)
        
        s_data_all = {}
        for s in new_list:
            s_weeks = {}
            for w in weeks_info: s_weeks[w["label"]] = create_weekly_df(w["dates"])
            s_data_all[s] = s_weeks
        st.session_state.student_weekly_data = s_data_all
        st.success("生徒リストと設定をリセットしました。")

# --- メインエリア ---
if st.session_state.teacher_weekly_data is None:
    st.info("👈 左のサイドバーで設定を行い、「入力を開始」ボタンを押してください。")
else:
    # モードに応じたタブ名と見出しの定義
    if mode == "新規作成":
        tab_names = ["📅 コーチシフト", "🔢 希望数", "🙋‍♂️ 生徒シフト", "🚀 作成＆結果"]
        header_req = "希望コマ数"
        header_shift = "生徒の行ける日時"
    else:
        tab_names = ["📅 コーチシフト(自動)", "🔢 追加希望数", "🙋‍♂️ 追加生徒シフト", "🚀 作成＆結果"]
        header_req = "追加したいコマ数"
        header_shift = "追加生徒の行ける日時"

    tab1, tab2, tab3, tab4 = st.tabs(tab_names)

    # =========================================
    # Tab 1: コーチシフト
    # =========================================
    with tab1:
        st.subheader(f"{teacher_name}コーチの予定")
        
        if mode == "追加作成(更新)":
            # === 追加作成モード: 入力スキップ ===
            st.info("🔄 追加作成のため、コーチのシフト入力は不要です。")
            st.write("「開講している空きコマ」に対して自動的に追加割り当てを行います。")
            
            # 裏側で自動的に「全開講」データをセットしておく
            # (計算ロジックがteacher_weekly_dataを参照するため)
            auto_teacher_data = {}
            for w in weeks_info:
                auto_teacher_data[w["label"]] = create_weekly_df(w["dates"])
            st.session_state.teacher_weekly_data = auto_teacher_data
            
            st.success("✅ 設定完了 (自動)")

        else:
            # === 新規作成モード: 通常の手動入力 ===
            with st.expander("⚡ 通常授業パターンから一括入力 (クリックで開く)"):
                st.write("通常授業の曜日・時間帯のみ表示しています。")
                st.write("「△」＝通常授業片配、「×」＝通常授業両配")
                st.caption("「適用」を押すと、冬期講習期間(12/24-1/5)以外の日付に反映されます。")
                
                weekdays_t = ["月", "火", "水", "木", "金", "土"]
                display_days = [1, 2, 3, 4, 5] # 火〜土
                valid_slots = {
                    1: [4, 5, 6], 2: [4, 5, 6], 3: [4, 5, 6], 4: [4, 5, 6], 
                    5: [2, 3, 4, 5]
                }
                options = ["×", "〇", "△"]
                std_pattern_t = {d: [None]*6 for d in display_days} 

                cols = st.columns([0.6] + [1]*5)
                cols[0].write("時間")
                for i, d_idx in enumerate(display_days):
                    cols[i+1].write(f"**{weekdays_t[d_idx]}**")
                
                for p in range(1, 7):
                    row_cols = st.columns([0.6] + [1]*5)
                    row_cols[0].write(f"**{p}講**")
                    
                    for i, d_idx in enumerate(display_days):
                        with row_cols[i+1]:
                            if p in valid_slots[d_idx]:
                                val = st.selectbox(
                                    f"t_{d_idx}_{p}", options, index=1, 
                                    key=f"std_teacher_{d_idx}_{p}", label_visibility="collapsed"
                                )
                                std_pattern_t[d_idx][p-1] = val
                            else:
                                st.write("-")

                if st.button("⚡ 先生のシフトに通常パターンを適用"):
                    current_data = st.session_state.teacher_weekly_data
                    new_data = apply_standard_schedule(current_data, std_pattern_t)
                    st.session_state.teacher_weekly_data = new_data
                    st.success("先生のシフトに通常パターンを適用しました！ (12/24-1/5は変更していません)")
                    st.rerun()
            st.divider()

            st.info("💡 個別の変更は以下のカレンダーで行い、最後に「保存」ボタンを押してください。")
            st.write("「〇」＝両配可、「△」＝片配可、「×」＝NG")
            
            with st.form("teacher_form"):
                updated_weekly_data = {}
                for w in weeks_info:
                    label = w["label"]
                    st.write(f"**{label}**")
                    df = st.session_state.teacher_weekly_data[label]
                    column_config = {}
                    options = ["〇", "×", "△"]
                    for col in df.columns:
                        column_config[col] = st.column_config.SelectboxColumn(col, options=options, width="small", required=True)
                    edited_df = st.data_editor(
                        df, column_config=column_config, use_container_width=True, key=f"teacher_edit_{label}", height=300
                    )
                    updated_weekly_data[label] = edited_df
                    st.divider()
                
                submitted = st.form_submit_button("💾 入力内容を保存する", type="primary")
                if submitted:
                    st.session_state.teacher_weekly_data = updated_weekly_data
                    st.success(f"{teacher_name}コーチのシフトを保存しました！")

    # --- Tab 2: 生徒希望数 ---
    with tab2:
        st.subheader(header_req)
        st.info("💡 入力後に必ず下の「保存」ボタンを押してください。")
        with st.form("req_form"):
            edited_req_df = st.data_editor(
                st.session_state.student_req_df, hide_index=True, use_container_width=True
            )
            submitted_req = st.form_submit_button("💾 希望数を保存する", type="primary")
            if submitted_req:
                st.session_state.student_req_df = edited_req_df
                st.success("生徒の希望数を保存しました！")

    # =========================================
    # Tab 3: 生徒シフト
    # =========================================
    with tab3:
        st.subheader(header_shift)
        target_student = st.selectbox("生徒を選択してください", st.session_state.student_list)
        
        if target_student:
            with st.expander("⚡ 通常授業パターンから一括入力 (クリックで開く)"):
                st.write("通常授業の曜日・時間帯のみ表示しています。")
                st.write("「〇」＝通常授業なし、「×」＝通常授業あり")
                st.caption("「適用」を押すと、冬期講習期間(12/24-1/5)以外の日付に反映されます。")
                
                weekdays = ["月", "火", "水", "木", "金", "土"]
                display_days = [1, 2, 3, 4, 5] # 火〜土
                valid_slots = {
                    1: [4, 5, 6], 2: [4, 5, 6], 3: [4, 5, 6], 4: [4, 5, 6], 
                    5: [2, 3, 4, 5]
                }
                options = ["×", "〇"]
                std_pattern = {d: [None]*6 for d in display_days}

                # --- ヘッダー ---
                cols = st.columns([0.6] + [1]*5)
                cols[0].write("時間")
                for i, d_idx in enumerate(display_days):
                    cols[i+1].write(f"**{weekdays[d_idx]}**")

                # --- 1〜6講ループ ---
                for p in range(1, 7):
                    row_cols = st.columns([0.6] + [1]*5)
                    row_cols[0].write(f"**{p}講**")
                    
                    for i, d_idx in enumerate(display_days):
                        with row_cols[i+1]:
                            if p in valid_slots[d_idx]:
                                val = st.selectbox(
                                    f"s_{target_student}_{d_idx}_{p}", options, index=1,
                                    key=f"std_{target_student}_{d_idx}_{p}", label_visibility="collapsed"
                                )
                                std_pattern[d_idx][p-1] = val
                            else:
                                st.write("-")

                if st.button(f"⚡ {target_student} の通常パターンを適用"):
                    current_data = st.session_state.student_weekly_data[target_student]
                    new_data = apply_standard_schedule(current_data, std_pattern)
                    st.session_state.student_weekly_data[target_student] = new_data
                    st.success(f"{target_student} の通常期間にパターンを適用しました！ (12/24-1/5は変更していません)")
                    st.rerun()

            st.divider()

            st.caption(f"{target_student} の行ける時間 (〇, △ = OK / × = NG)")
            st.info("💡 個別の変更は以下のカレンダーで行い、最後に「保存」ボタンを押してください。")
            st.write("「〇」＝空き、「×」＝NG")
            
            with st.form(f"student_form_{target_student}"):
                updated_s_weekly = {}
                for w in weeks_info:
                    label = w["label"]
                    st.write(f"**{label}**")
                    s_df = st.session_state.student_weekly_data[target_student][label]
                    column_config_s = {}
                    options = ["〇", "×"]
                    for col in s_df.columns:
                        column_config_s[col] = st.column_config.SelectboxColumn(col, options=options, width="small", required=True)
                    edited_s_df = st.data_editor(
                        s_df, column_config=column_config_s, use_container_width=True,
                        key=f"student_edit_{target_student}_{label}", height=300
                    )
                    updated_s_weekly[label] = edited_s_df
                    st.divider()
                
                submitted_s = st.form_submit_button(f"💾 {target_student} のシフトを保存する", type="primary")
                if submitted_s:
                    st.session_state.student_weekly_data[target_student] = updated_s_weekly
                    st.success(f"{target_student} のシフトを保存しました！")

    # --- Tab 4: 作成実行 & 結果表示 ---
    with tab4:
        st.subheader("時間割作成")
        
        if mode == "追加作成(更新)" and st.session_state.existing_schedule_map is None:
            st.error("⛔ Excelファイルがアップロードされていません。サイドバーからアップロードしてください。")
        else:
            if st.button("🚀 作成スタート", type="primary"):
                with st.spinner("計算中..."):
                    try:
                        schedule_map, all_dates, unscheduled = calculate_schedule(
                            st.session_state.teacher_weekly_data,
                            st.session_state.student_req_df,
                            st.session_state.student_weekly_data,
                            teacher_name,
                            existing_schedule_map=st.session_state.existing_schedule_map
                        )
                        
                        st.success("✅ 完成しました！ 結果は以下に表示されます。")
                        
                        # === A. 画面表示 ===
                        st.divider()
                        st.subheader("📅 完成時間割プレビュー")
                        
                        start_date = datetime.date(2025, 12, 1)
                        end_date = datetime.date(2026, 1, 31)
                        cal_dates = []
                        curr = start_date
                        while curr <= end_date:
                            cal_dates.append(curr)
                            curr += datetime.timedelta(days=1)

                        for i in range(0, len(cal_dates), 7):
                            week_dates = cal_dates[i : i+7]
                            week_data = {}
                            col_names = [d.strftime("%m/%d(%a)") for d in week_dates]
                            col_config = {}

                            for d_obj, col in zip(week_dates, col_names):
                                col_config[col] = st.column_config.TextColumn(col, width="medium")
                                col_content = []
                                for p in range(1, 7):
                                    assigned = schedule_map.get((d_obj, p), [])
                                    if assigned:
                                        col_content.append(", ".join(assigned))
                                    else:
                                        open_periods = get_open_periods(d_obj)
                                        col_content.append("-" if p in open_periods else "×")
                                week_data[col] = col_content
                            
                            df_week_view = pd.DataFrame(week_data, index=[f"{p}講" for p in range(1, 7)])
                            st.write(f"**{week_dates[0].strftime('%Y/%m/%d')} 週**")
                            st.dataframe(df_week_view, column_config=col_config, use_container_width=True)
                            st.write("") 

                        if unscheduled:
                            st.error("⚠️ 入りきらなかった授業があります")
                            st.dataframe(pd.DataFrame(unscheduled), hide_index=True)
                        else:
                            st.info("🎉 全ての授業が割り当てられました！")

                        # === B. Excel出力 ===
                        st.divider()
                        output = io.BytesIO()
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                            workbook = writer.book
                            worksheet = workbook.add_worksheet("時間割")
                            writer.sheets["時間割"] = worksheet
                            wrap_fmt = workbook.add_format({'text_wrap': True, 'valign': 'top', 'border': 1, 'align': 'center'})
                            header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'align': 'center'})
                            
                            current_row = 0
                            for i in range(0, len(cal_dates), 7):
                                week_dates = cal_dates[i : i+7]
                                worksheet.write(current_row, 0, "講", header_fmt)
                                for col_idx, d_obj in enumerate(week_dates):
                                    worksheet.write(current_row, col_idx + 1, d_obj.strftime("%m/%d(%a)"), header_fmt)
                                for p in range(1, 7):
                                    row_idx = current_row + p
                                    worksheet.write(row_idx, 0, p, wrap_fmt)
                                    for col_idx, d_obj in enumerate(week_dates):
                                        assigned = schedule_map.get((d_obj, p), [])
                                        cell_text = "\n".join(assigned) if assigned else ("" if p in get_open_periods(d_obj) else "×")
                                        worksheet.write(row_idx, col_idx + 1, cell_text, wrap_fmt)
                                current_row += 8
                            worksheet.set_column(0, 0, 5); worksheet.set_column(1, 7, 18)
                            
                            if unscheduled: pd.DataFrame(unscheduled).to_excel(writer, sheet_name="未消化リスト", index=False)

                        st.download_button(
                            label="📥 結果をExcelで保存",
                            data=output.getvalue(),
                            file_name=f"完成時間割_{teacher_name}_{datetime.date.today()}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )

                    except Exception as e:
                        st.error(f"エラーが発生しました: {e}")