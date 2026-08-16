import streamlit as st
import pandas as pd
import io
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Auditoria Contábil - Domínio Sistemas", layout="wide")

def formatar_moeda(v):
    try:
        if isinstance(v, str):
            v = v.replace('.', '').replace(',', '.')
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')


def formatar_brl(valor):
    """Formata número float/Decimal para R$ 1.234,56"""
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ==========================================
# MÓDULOS DE CACHE E LEITURA (ROBUSTEZ)
# ==========================================
@st.cache_data
def extrair_saldo_balancete(raw_data, conta_alvo):
    try:
        df_balancete = pd.read_excel(io.BytesIO(raw_data))
    except:
        tryimport streamlit as st
import pandas as pd
import io
import re
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict, deque
from itertools import combinations

st.set_page_config(page_title="Auditoria Contábil - Domínio Sistemas", layout="wide")

def formatar_moeda(v):
    try:
        if isinstance(v, str):
            v = v.replace('.', '').replace(',', '.')
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')


def formatar_brl(valor):
    """Formata número float/Decimal para R$ 1.234,56"""
    return f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ==========================================
# MÓDULOS DE CACHE E LEITURA (ROBUSTEZ)
# ==========================================
@st.cache_data
def extrair_saldo_balancete(raw_data, conta_alvo):
    try:
        df_balancete = pd.read_excel(io.BytesIO(raw_data))
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_balancete = dfs[0]
        except:
            raise ValueError("O formato do Balancete não é suportado ou está corrompido.")

    cols_str = ' '.join(str(c).upper() for c in df_balancete.columns)

    if ('CÓDIGO' in cols_str or 'CODIGO' in cols_str or 'CONTA' in cols_str) and 'ANTERIOR' in cols_str:
        df_bal = df_balancete.copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.columns]
    else:
        header_idx = -1
        for idx, row in df_balancete.head(20).iterrows():
            row_str = ' '.join(str(x).upper() for x in row.values)
            if ('CÓDIGO' in row_str or 'CODIGO' in row_str or 'CONTA' in row_str) and 'ANTERIOR' in row_str:
                header_idx = idx
                break
        if header_idx == -1:
            return Decimal('0.00')
        df_bal = df_balancete.iloc[header_idx+1:].copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.iloc[header_idx].values]

    col_conta = next((c for c in df_bal.columns if 'CÓDIGO' in c or 'CODIGO' in c or 'CONTA' in c), None)
    col_saldo = next((c for c in df_bal.columns if 'ANTERIOR' in c), None)
    if not col_conta or not col_saldo:
        return Decimal('0.00')

    df_bal[col_conta] = pd.to_numeric(df_bal[col_conta], errors='coerce')
    linha_conta = df_bal[df_bal[col_conta] == float(conta_alvo)]
    if linha_conta.empty:
        return Decimal('0.00')
    return formatar_moeda(linha_conta.iloc[0][col_saldo])


@st.cache_data
def carregar_base_lancamentos(raw_data):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data))
            df_raw = dfs[0]
        except:
            raise ValueError("Erro de leitura. Certifique-se de que é um Excel (.xlsx ou .xls) válido.")

    header_idx = 5
    for idx, row in df_raw.head(20).iterrows():
        row_str = ' '.join(str(x).upper() for x in row.values)
        if 'DATA' in row_str and 'HIST' in row_str:
            header_idx = idx
            break

    df_clean = df_raw.iloc[header_idx:].copy()
    df_clean.columns = [str(h).strip() for h in df_clean.iloc[0].values]
    df_clean = df_clean.iloc[1:].dropna(how='all', axis=1).dropna(how='all', axis=0).reset_index(drop=True)

    cols = pd.Series(df_clean.columns)
    for dup in cols[cols.duplicated()].unique():
        cols[cols[cols == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols == dup))]
    df_clean.columns = cols

    df_main = df_clean[df_clean['Data'].notna()].copy()
    df_main = df_main[~df_main['Data'].astype(str).str.startswith('Conta:')].copy()
    df_main['Valor'] = df_main['Valor'].apply(formatar_moeda)
    df_main['Data'] = pd.to_datetime(df_main['Data'], errors='coerce')

    if 'Débito' in df_main.columns:
        df_main['Débito'] = pd.to_numeric(df_main['Débito'], errors='coerce')
    if 'Crédito' in df_main.columns:
        df_main['Crédito'] = pd.to_numeric(df_main['Crédito'], errors='coerce')

    return df_main


# ==========================================
# MOTOR DE CONCILIAÇÃO (v5)
# Etapa 1: saldo anterior | Etapa 2: direto 1:1 | Etapa 3: grupo por NF-e (seguro)
# Etapa 4: grupo por janela curta - opcional, para casos sem identificador (cartão)
# ==========================================
def montar_tabela_razao(eventos):
    razao = []
    saldo = Decimal('0.00')
    for ev in eventos:
        saldo += ev['Débito']
        saldo -= ev['Crédito']
        razao.append({
            'Data': ev['Data'],
            'Histórico': ev['Histórico'],
            'Débito (+)': float(ev['Débito']) if ev['Débito'] > 0 else None,
            'Crédito (-)': float(ev['Crédito']) if ev['Crédito'] > 0 else None,
            'Saldo Acumulado': float(saldo)
        })
    return razao, float(saldo)


def _fechar_saldo_anterior(saldo_ant, creditos):
    """Fecha o saldo anterior (débito único) contra uma combinação de créditos em
    sequência cuja soma bata exatamente, permitindo 1 crédito 'fora da ordem'."""
    if saldo_ant <= 0 or not creditos:
        return []
    acumulado = Decimal('0.00')
    indices = []
    for idx, c in enumerate(creditos):
        acumulado += c['Crédito']
        indices.append(idx)
        if acumulado == saldo_ant:
            return indices
        if acumulado > saldo_ant:
            diff = acumulado - saldo_ant
            for ci in indices:
                if creditos[ci]['Crédito'] == diff:
                    return [i for i in indices if i != ci]
            return []
    return []


def _matching_direto(debitos_idx, creditos_idx, todos_debitos, todos_creditos):
    """Casa 1 débito com 1 crédito de valor EXATAMENTE igual (fila FIFO por valor)."""
    fila_por_valor = defaultdict(deque)
    for i in creditos_idx:
        fila_por_valor[todos_creditos[i]['Crédito']].append(i)

    debitos_usados = set()
    creditos_usados = set()
    for i in debitos_idx:
        valor = todos_debitos[i]['Débito']
        fila = fila_por_valor.get(valor)
        if fila:
            j = fila.popleft()
            debitos_usados.add(i)
            creditos_usados.add(j)

    return debitos_usados, creditos_usados


def _extrair_chave_documento(historico):
    """Extrai o número da NF-e do histórico (ex: 'NF-e 613432' -> '613432')."""
    m = re.search(r'NF-?e[\s\-\.:]*?(\d+)', str(historico), re.IGNORECASE)
    return m.group(1) if m else None


def _matching_por_nfe(debitos_idx, creditos_idx, todos_debitos, todos_creditos,
                       max_parcelas=6, janela_dias=120, max_candidatos=25):
    """
    ETAPA 3 (segura) - Agrupa débitos que compartilham o MESMO número de NF-e
    (nota dividida em CFOPs) e tenta fechar o total do grupo contra uma
    combinação de créditos (parcelas) dentro de uma janela de dias após a nota.
    Como o agrupamento usa um identificador exato (nº da NF-e), não há risco de
    juntar lançamentos de notas diferentes por coincidência.
    """
    grupos = defaultdict(list)
    for i in debitos_idx:
        chave = _extrair_chave_documento(todos_debitos[i]['Histórico'])
        if chave:
            grupos[chave].append(i)

    debitos_usados = set()
    creditos_usados = set()

    for chave, idxs in grupos.items():
        total_grupo = sum((todos_debitos[i]['Débito'] for i in idxs), Decimal('0.00'))
        data_nota = max(pd.to_datetime(todos_debitos[i]['Data Real'], format='%d/%m/%Y') for i in idxs)

        candidatos = [
            j for j in creditos_idx
            if j not in creditos_usados
            and 0 <= (pd.to_datetime(todos_creditos[j]['Data Real'], format='%d/%m/%Y') - data_nota).days <= janela_dias
        ]
        candidatos.sort(key=lambda j: pd.to_datetime(todos_creditos[j]['Data Real'], format='%d/%m/%Y'))
        candidatos = candidatos[:max_candidatos]

        if not candidatos:
            continue

        encontrado = None
        for tam in range(1, min(max_parcelas, len(candidatos)) + 1):
            for combo in combinations(candidatos, tam):
                if sum((todos_creditos[j]['Crédito'] for j in combo), Decimal('0.00')) == total_grupo:
                    encontrado = combo
                    break
            if encontrado:
                break

        if encontrado:
            debitos_usados.update(idxs)
            creditos_usados.update(encontrado)

    return debitos_usados, creditos_usados


def _matching_por_janela_curta(debitos_idx, creditos_idx, todos_debitos, todos_creditos,
                                janela_dias=5, max_grupo=5, max_candidatos=10):
    """
    ETAPA 4 (opcional, use com cautela) - Para lançamentos SEM identificador em
    comum (ex: vendas de cartão + taxa + recebimento em lote da operadora).
    Busca, dentro de uma janela curta de dias, uma combinação de débitos cuja
    soma bata com uma combinação de créditos. Sem um identificador exato, existe
    risco (baixo, mas real) de casar lançamentos que apenas coincidem em valor -
    por isso a janela é curta e o resultado fica marcado para revisão.
    """
    debitos_usados = set()
    creditos_usados = set()

    debitos_ordenados = sorted(debitos_idx, key=lambda i: pd.to_datetime(todos_debitos[i]['Data Real'], format='%d/%m/%Y'))

    for i0 in debitos_ordenados:
        if i0 in debitos_usados:
            continue
        data_ref = pd.to_datetime(todos_debitos[i0]['Data Real'], format='%d/%m/%Y')

        deb_janela = [
            i for i in debitos_idx
            if i not in debitos_usados
            and abs((pd.to_datetime(todos_debitos[i]['Data Real'], format='%d/%m/%Y') - data_ref).days) <= janela_dias
        ][:max_candidatos]

        cred_janela = [
            j for j in creditos_idx
            if j not in creditos_usados
            and 0 <= (pd.to_datetime(todos_creditos[j]['Data Real'], format='%d/%m/%Y') - data_ref).days <= janela_dias
        ][:max_candidatos]

        if not deb_janela or not cred_janela:
            continue

        encontrado_d, encontrado_c = None, None
        for tam_c in range(1, min(max_grupo, len(cred_janela)) + 1):
            for combo_c in combinations(cred_janela, tam_c):
                alvo = sum((todos_creditos[j]['Crédito'] for j in combo_c), Decimal('0.00'))
                for tam_d in range(1, min(max_grupo, len(deb_janela)) + 1):
                    for combo_d in combinations(deb_janela, tam_d):
                        if sum((todos_debitos[i]['Débito'] for i in combo_d), Decimal('0.00')) == alvo:
                            encontrado_d, encontrado_c = combo_d, combo_c
                            break
                    if encontrado_d:
                        break
                if encontrado_d:
                    break
            if encontrado_d:
                break

        if encontrado_d:
            debitos_usados.update(encontrado_d)
            creditos_usados.update(encontrado_c)

    return debitos_usados, creditos_usados


def processar_razoes_contabeis(df_main, conta_alvo, saldo_anterior_informado, tipo,
                                usar_matching_nfe=True, janela_nfe_dias=120, max_parcelas_nfe=6,
                                usar_matching_janela=False, janela_curta_dias=5, max_grupo_janela=5):
    if tipo == 'CARTAO':
        col_deb, col_cred = 'Débito', 'Crédito'
    else:
        col_deb, col_cred = 'Crédito', 'Débito'

    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))

    todos_debitos = []
    todos_creditos = []

    tem_saldo_anterior = saldo_ant > Decimal('0.00')
    if tem_saldo_anterior:
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        todos_debitos.append({
            'Data Real': (data_base - pd.Timedelta(days=1)).strftime('%d/%m/%Y'), 'Data': 'Saldo Anterior',
            'Histórico': 'SALDO ANTERIOR HERDADO', 'Débito': saldo_ant, 'Crédito': Decimal('0.00')
        })

    for idx, row in df_alvo.iterrows():
        dt_str = row['Data'].strftime('%d/%m/%Y')
        valor = Decimal(str(row['Valor']))
        if row[col_deb] == conta_alvo:
            todos_debitos.append({'Data Real': dt_str, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': valor, 'Crédito': Decimal('0.00')})
        if row[col_cred] == conta_alvo:
            todos_creditos.append({'Data Real': dt_str, 'Data': dt_str, 'Histórico': row['Histórico'], 'Débito': Decimal('0.00'), 'Crédito': valor})

    todos_debitos.sort(key=lambda x: pd.to_datetime(x['Data Real'], format='%d/%m/%Y'))
    todos_creditos.sort(key=lambda x: pd.to_datetime(x['Data Real'], format='%d/%m/%Y'))

    # ETAPA 1: fecha o saldo anterior (se houver) contra uma combinação de créditos
    indices_credito_ant = []
    if tem_saldo_anterior:
        indices_credito_ant = _fechar_saldo_anterior(saldo_ant, todos_creditos)
    saldo_anterior_fechado = tem_saldo_anterior and len(indices_credito_ant) > 0

    debitos_pool_idx = list(range(1, len(todos_debitos))) if saldo_anterior_fechado else list(range(len(todos_debitos)))
    creditos_pool_idx = [i for i in range(len(todos_creditos)) if i not in set(indices_credito_ant)]

    # ETAPA 2: matching direto valor-a-valor (1 débito = 1 crédito)
    debitos_usados, creditos_usados = _matching_direto(debitos_pool_idx, creditos_pool_idx, todos_debitos, todos_creditos)
    debitos_restantes = [i for i in debitos_pool_idx if i not in debitos_usados]
    creditos_restantes = [i for i in creditos_pool_idx if i not in creditos_usados]

    # ETAPA 3: matching em grupo por NF-e (seguro - usa identificador exato)
    if usar_matching_nfe:
        d_nfe, c_nfe = _matching_por_nfe(
            debitos_restantes, creditos_restantes, todos_debitos, todos_creditos,
            max_parcelas=max_parcelas_nfe, janela_dias=janela_nfe_dias
        )
        debitos_usados |= d_nfe
        creditos_usados |= c_nfe
        debitos_restantes = [i for i in debitos_restantes if i not in d_nfe]
        creditos_restantes = [i for i in creditos_restantes if i not in c_nfe]

    # ETAPA 4: matching em grupo por janela curta (opcional, sem identificador - ex: cartão)
    itens_marcados_revisao = set()
    if usar_matching_janela:
        d_jan, c_jan = _matching_por_janela_curta(
            debitos_restantes, creditos_restantes, todos_debitos, todos_creditos,
            janela_dias=janela_curta_dias, max_grupo=max_grupo_janela
        )
        debitos_usados |= d_jan
        creditos_usados |= c_jan
        itens_marcados_revisao = d_jan | c_jan
        debitos_restantes = [i for i in debitos_restantes if i not in d_jan]
        creditos_restantes = [i for i in creditos_restantes if i not in c_jan]

    debitos_pend_idx = debitos_restantes
    creditos_pend_idx = creditos_restantes

    # marca no histórico os itens conciliados via janela curta, para revisão
    for i in itens_marcados_revisao:
        pass  # marcação feita na montagem abaixo

    def marcar(ev, idx_original):
        if idx_original in itens_marcados_revisao:
            ev = dict(ev)
            ev['Histórico'] = '🔎 ' + ev['Histórico']
        return ev

    eventos_ant = []
    if saldo_anterior_fechado:
        eventos_ant = [todos_debitos[0]] + [todos_creditos[i] for i in indices_credito_ant]

    eventos_atual = (
        [marcar(todos_debitos[i], i) for i in debitos_usados]
        + [marcar(todos_creditos[i], i) for i in creditos_usados]
    )
    eventos_pend = [todos_debitos[i] for i in debitos_pend_idx] + [todos_creditos[i] for i in creditos_pend_idx]

    chave_ordem = lambda x: (pd.to_datetime(x['Data Real'], format='%d/%m/%Y'), x['Crédito'] > 0)
    eventos_ant.sort(key=chave_ordem)
    eventos_atual.sort(key=chave_ordem)
    eventos_pend.sort(key=chave_ordem)
    todos_eventos = sorted(todos_debitos + todos_creditos, key=chave_ordem)

    razao_tot, saldo_tot = montar_tabela_razao(todos_eventos)
    razao_ant, saldo_ant_aba = montar_tabela_razao(eventos_ant)
    razao_atual, saldo_atual_aba = montar_tabela_razao(eventos_atual)
    razao_pend, saldo_pend_aba = montar_tabela_razao(eventos_pend)

    return razao_tot, razao_ant, razao_atual, razao_pend, saldo_tot, saldo_ant_aba, saldo_atual_aba, saldo_pend_aba


def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data:
                pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()


# ==========================================
# INTERFACE DE USUÁRIO (STREAMLIT)
# ==========================================
st.title("📊 Auditoria Contábil - Domínio Sistemas")
st.markdown("Emissão Analítica de Livros Razão. Matching direto + grupo por NF-e + grupo por janela (opcional).")

with st.sidebar:
    st.subheader("⚙️ Configurações de Matching")
    usar_nfe = st.checkbox("Agrupar por NF-e (CFOP dividido + parcelamento)", value=True)
    janela_nfe = st.number_input("Janela de dias após a nota", value=120, step=10)
    max_parcelas = st.number_input("Máx. de parcelas por grupo NF-e", value=6, step=1, min_value=1)
    st.markdown("---")
    usar_janela = st.checkbox("Agrupar por janela curta (sem identificador, ex: cartão) ⚠️", value=False)
    st.caption("Sem número de documento em comum. Use com cautela — itens conciliados assim vêm marcados com 🔎 para revisão.")
    janela_curta = st.number_input("Janela de dias", value=5, step=1)
    max_grupo_janela = st.number_input("Máx. de itens por grupo", value=5, step=1, min_value=1)

modo = st.radio("Selecione a Natureza da Conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
conta_input = st.number_input("Digite a conta contábil alvo (Ex: 623 ou 1059)", value=0, step=1)

st.markdown("---")
col_arq1, col_arq2 = st.columns(2)
with col_arq1:
    arquivo_lancamentos = st.file_uploader("📁 Anexe a Base Geral de Lançamentos (.xls ou .xlsx)", type=["xls", "xlsx"])
with col_arq2:
    arquivo_balancete = st.file_uploader("📁 Anexe o Balancete Opcional (.xls ou .xlsx)", type=["xls", "xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de {formatar_brl(saldo_abertura_var)} capturado do Balancete.")
    except Exception as e:
        st.error("Erro ao analisar o Balancete. Verifique o formato do arquivo.")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)

        tem_na_base = (df_base_geral['Débito'] == conta_input).any() or (df_base_geral['Crédito'] == conta_input).any()
        if not tem_na_base:
            st.error(f"❌ EXECUÇÃO BLOQUEADA: A conta {conta_input} não possui lançamentos no arquivo anexado. Digite o número correto.")
            st.stop()

        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'

        r_tot, r_ant, r_atual, r_pend, s_tot, s_ant, s_atual, s_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria,
            usar_matching_nfe=usar_nfe, janela_nfe_dias=janela_nfe, max_parcelas_nfe=max_parcelas,
            usar_matching_janela=usar_janela, janela_curta_dias=janela_curta, max_grupo_janela=max_grupo_janela
        )

        if len(r_ant) == 0 and len(r_atual) == 0:
            st.error("🚨 ATENÇÃO: NENHUMA CONCILIAÇÃO OCORREU. Não foram encontrados pares/grupos que fechem na base enviada.")

        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_tot,
            '2. Conciliado (Ano Anterior)': r_ant,
            '3. Conciliado (Atual)': r_atual,
            '4. Não Conciliado (Pendente)': r_pend
        })

        st.download_button(label="📥 Baixar 4 Razões (Excel)", data=excel_data,
                           file_name=f"Auditoria_4Razoes_{conta_input}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.write("---")
        st.subheader("Balanço de Validação das Abas")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📘 Total", formatar_brl(s_tot))
        col2.metric("✅ Conciliado (Ant)", formatar_brl(s_ant))
        col3.metric("✅ Conciliado (Atual)", formatar_brl(s_atual))
        col4.metric("⚠️ Pendente", formatar_brl(s_pend))

        if abs(s_ant) == 0.0 and abs(s_atual) == 0.0 and round(s_tot, 2) == round(s_pend, 2):
            st.success("✅ **Auditoria Validada:** As abas de itens conciliados fecharam em R$ 0,00 perfeitamente.")

        if usar_janela:
            st.info("🔎 Itens marcados com essa lupa na aba 'Conciliado (Atual)' foram casados por proximidade de data, sem identificador exato — vale conferir manualmente.")

    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
