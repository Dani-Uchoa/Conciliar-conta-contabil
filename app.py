import streamlit as st
import pandas as pd
import io
import re
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict, deque
from itertools import combinations  # usado apenas no matching por NF-e

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
    except Exception:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data).read())
            df_balancete = dfs[0]
        except Exception:
            raise ValueError("O formato do Balancete não é suportado ou está corrompido. Confira se o arquivo é um .xls/.xlsx válido do Domínio.")

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
def extrair_cabecalho(raw_data):
    """Lê nome da empresa, período/competência do relatório e o nome de cada
    conta contábil presente no arquivo (linhas 'Conta: NNN - NOME')."""
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except Exception:
        return None, None, {}

    empresa = None
    if len(df_raw) > 0 and not pd.isna(df_raw.iloc[0, 0]):
        primeira = str(df_raw.iloc[0, 0]).strip()
        if primeira and not primeira.upper().startswith(('CONTA:', 'DATA', 'C.N.P.J')):
            empresa = primeira

    periodo = None
    contas_nomes = {}
    for val in df_raw[0]:
        if pd.isna(val):
            continue
        v = str(val).strip()
        vu = v.upper()
        if periodo is None and (vu.startswith('PERÍODO') or vu.startswith('PERIODO')):
            periodo = v.split(':', 1)[1].strip() if ':' in v else v
        if vu.startswith('CONTA:'):
            resto = v.split(':', 1)[1].strip() if ':' in v else ''
            if '-' in resto:
                num_str, nome = resto.split('-', 1)
                num_str = num_str.strip()
                if num_str.isdigit():
                    contas_nomes[int(num_str)] = nome.strip()

    return empresa, periodo, contas_nomes


@st.cache_data
def carregar_base_lancamentos(raw_data):
    try:
        df_raw = pd.read_excel(io.BytesIO(raw_data), header=None)
    except Exception:
        try:
            dfs = pd.read_html(io.BytesIO(raw_data))
            df_raw = dfs[0]
        except Exception:
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
# MOTOR DE CONCILIAÇÃO (v7 — cartões com máscaras de bits)
# Regras confirmadas:
#  - crédito nunca pode ser anterior ao débito (cronológico)
#  - janela máxima de dias entre provisão e baixa (padrão 60, configurável)
#  - sempre soma EXATA (nunca sobra resto) - N débitos podem casar com M créditos
#  - se algo não fecha, segue o loop e joga pra pendente (não trava tudo)
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


def _dentro_da_janela(data_deb, data_cred, janela_dias):
    diff = (data_cred - data_deb).days
    return 0 <= diff <= janela_dias


def _matching_direto(debitos_idx, creditos_idx, todos_debitos, todos_creditos, janela_dias):
    """Casa 1 débito com 1 crédito de valor EXATAMENTE igual, respeitando a
    janela cronológica (crédito no mesmo dia ou depois do débito, até N dias)."""
    creditos_por_valor = defaultdict(list)
    for j in creditos_idx:
        creditos_por_valor[todos_creditos[j]['Crédito']].append(j)
    for valor in creditos_por_valor:
        creditos_por_valor[valor].sort(key=lambda j: todos_creditos[j]['Data_dt'])

    debitos_usados = set()
    creditos_usados = set()

    for i in sorted(debitos_idx, key=lambda i: todos_debitos[i]['Data_dt']):
        valor = todos_debitos[i]['Débito']
        data_d = todos_debitos[i]['Data_dt']
        candidatos = creditos_por_valor.get(valor, [])
        for pos, j in enumerate(candidatos):
            if j in creditos_usados:
                continue
            data_c = todos_creditos[j]['Data_dt']
            if _dentro_da_janela(data_d, data_c, janela_dias):
                debitos_usados.add(i)
                creditos_usados.add(j)
                break

    return debitos_usados, creditos_usados


def _extrair_chave_documento(historico):
    """Extrai o número da NF-e do histórico (ex: 'NF-e 613432' -> '613432')."""
    m = re.search(r'NF-?e[\s\-\.:]*?(\d+)', str(historico), re.IGNORECASE)
    return m.group(1) if m else None


def _matching_por_nfe(debitos_idx, creditos_idx, todos_debitos, todos_creditos,
                       janela_dias=60, max_parcelas=6, max_candidatos=25):
    """
    Agrupa débitos que compartilham o MESMO número de NF-e (nota dividida em
    CFOPs) e tenta fechar o total do grupo contra uma combinação de créditos
    (parcelas) dentro da janela de dias, sempre para frente no tempo.
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
        data_nota = max(todos_debitos[i]['Data_dt'] for i in idxs)

        candidatos = [
            j for j in creditos_idx
            if j not in creditos_usados
            and _dentro_da_janela(data_nota, todos_creditos[j]['Data_dt'], janela_dias)
        ]
        candidatos.sort(key=lambda j: todos_creditos[j]['Data_dt'])
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


from decimal import Decimal


def centavos_exatos(valor):
    """Converte um valor monetário já normalizado em centavos inteiros."""
    if isinstance(valor, Decimal):
        return int(valor.quantize(Decimal('0.01')) * 100)
    return int(Decimal(str(valor)).quantize(Decimal('0.01')) * 100)


def reach_bitset(valores, max_itens):
    """Calcula somas alcançáveis por quantidade de itens usando inteiros.

    No resultado, `resultado[k]` é um inteiro em que o bit `s` vale 1 quando
    existe um subconjunto de exatamente `k` valores cuja soma é `s` centavos.
    A atualização `bitset |= bitset << valor` é feita pelo runtime em operações
    de inteiros, sem enumerar combinações de índices em Python.
    """
    limite = max(0, min(int(max_itens), len(valores)))
    resultado = [0] * (limite + 1)
    resultado[0] = 1
    for pos, valor in enumerate(valores):
        valor = int(valor)
        if valor <= 0:
            continue
        topo = min(limite, pos + 1)
        for k in range(topo, 0, -1):
            resultado[k] |= resultado[k - 1] << valor
    return resultado


def _recuperar_subconjunto(valores, alvo, max_itens, quantidade_preferida=None):
    """Recupera índices locais de uma soma encontrada pelo bitset."""
    alvo = int(alvo)
    if alvo < 0 or not valores or max_itens <= 0:
        return None

    n = len(valores)
    limite = min(int(max_itens), n)
    sufixo = [[0] * (limite + 1) for _ in range(n + 1)]
    sufixo[n][0] = 1
    for pos in range(n - 1, -1, -1):
        sufixo[pos][0] = 1
        valor = int(valores[pos])
        for k in range(1, min(limite, n - pos) + 1):
            sufixo[pos][k] = sufixo[pos + 1][k] | (sufixo[pos + 1][k - 1] << valor)

    if quantidade_preferida is not None:
        quantidades = [int(quantidade_preferida)]
    else:
        quantidades = range(1, limite + 1)
    quantidade = next(
        (
            k for k in quantidades
            if 0 <= k <= limite and ((sufixo[0][k] >> alvo) & 1)
        ),
        None,
    )
    if quantidade is None:
        return None

    restante = alvo
    faltam = quantidade
    mascara = 0
    for pos, valor in enumerate(valores):
        valor = int(valor)
        if faltam == 0:
            break
        pode_pular = (
            ((sufixo[pos + 1][faltam] >> restante) & 1)
            if faltam <= limite else 0
        )
        if pode_pular:
            continue
        if (
            restante >= valor
            and ((sufixo[pos + 1][faltam - 1] >> (restante - valor)) & 1)
        ):
            mascara |= 1 << pos
            restante -= valor
            faltam -= 1
        else:
            return None
    return mascara if restante == 0 and faltam == 0 else None


def _matching_por_janela(
    debitos_idx,
    creditos_idx,
    todos_debitos,
    todos_creditos,
    janela_dias=60,
    max_grupo=6,
    max_candidatos=14,
):
    """Concilia cartões por janela com subset-sum em máscaras de bits.

    A semântica é equivalente ao fallback original: crédito não pode ser
    anterior ao débito; primeiro tenta o débito isolado e depois grupos que
    incluem o débito corrente; cada item só pode ser usado uma vez.
    """
    debitos_usados = set()
    creditos_usados = set()
    debitos_ordenados = sorted(debitos_idx, key=lambda i: todos_debitos[i]['Data_dt'])

    for i0 in debitos_ordenados:
        if i0 in debitos_usados:
            continue
        data_ref = todos_debitos[i0]['Data_dt']
        deb_janela = [i0] + [
            i for i in debitos_idx
            if i != i0
            and i not in debitos_usados
            and 0 <= (todos_debitos[i]['Data_dt'] - data_ref).days <= janela_dias
        ]
        deb_janela = deb_janela[:max_candidatos]

        cred_janela = [
            j for j in creditos_idx
            if j not in creditos_usados
            and 0 <= (todos_creditos[j]['Data_dt'] - data_ref).days <= janela_dias
        ]
        cred_janela.sort(key=lambda j: todos_creditos[j]['Data_dt'])
        cred_janela = cred_janela[:max_candidatos]
        if not cred_janela:
            continue

        valores_creditos = [
            centavos_exatos(todos_creditos[j]['Crédito']) for j in cred_janela
        ]
        alvo_solo = centavos_exatos(todos_debitos[i0]['Débito'])
        mascara_creditos = _recuperar_subconjunto(
            valores_creditos, alvo_solo, max_grupo
        )
        if mascara_creditos is not None:
            encontrados = [
                cred_janela[pos]
                for pos in range(len(cred_janela))
                if (mascara_creditos >> pos) & 1
            ]
            debitos_usados.add(i0)
            creditos_usados.update(encontrados)
            continue

        if len(deb_janela) < 2:
            continue

        valor_base = centavos_exatos(todos_debitos[i0]['Débito'])
        valores_outros_debitos = [
            centavos_exatos(todos_debitos[i]['Débito']) for i in deb_janela[1:]
        ]
        alcancaveis_debitos = reach_bitset(
            valores_outros_debitos, max(0, max_grupo - 1)
        )
        alcancaveis_creditos = reach_bitset(valores_creditos, max_grupo)
        creditos_qualquer_quantidade = 0
        for k in range(1, len(alcancaveis_creditos)):
            creditos_qualquer_quantidade |= alcancaveis_creditos[k]

        encontrado = None
        for tamanho_debitos in range(
            2, min(max_grupo, len(deb_janela)) + 1
        ):
            tamanho_outros = tamanho_debitos - 1
            somas_comuns = (
                alcancaveis_debitos[tamanho_outros] << valor_base
            ) & creditos_qualquer_quantidade
            if not somas_comuns:
                continue

            bit_menor = somas_comuns & -somas_comuns
            alvo = bit_menor.bit_length() - 1
            mascara_outros = _recuperar_subconjunto(
                valores_outros_debitos,
                alvo - valor_base,
                tamanho_outros,
                quantidade_preferida=tamanho_outros,
            )
            mascara_creditos = _recuperar_subconjunto(
                valores_creditos, alvo, max_grupo
            )
            if mascara_outros is not None and mascara_creditos is not None:
                encontrado = mascara_outros, mascara_creditos
                break

        if encontrado is None:
            continue

        mascara_outros, mascara_creditos = encontrado
        encontrados_debitos = [i0] + [
            deb_janela[pos + 1]
            for pos in range(len(valores_outros_debitos))
            if (mascara_outros >> pos) & 1
        ]
        encontrados_creditos = [
            cred_janela[pos]
            for pos in range(len(cred_janela))
            if (mascara_creditos >> pos) & 1
        ]
        debitos_usados.update(encontrados_debitos)
        creditos_usados.update(encontrados_creditos)

    return debitos_usados, creditos_usados



def processar_razoes_contabeis(df_main, conta_alvo, saldo_anterior_informado, tipo,
                                janela_dias=60, usar_matching_nfe=True, usar_matching_janela=True,
                                max_parcelas_nfe=6, max_grupo_janela=6):
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
        data_ant = data_base - pd.Timedelta(days=1)
        todos_debitos.append({
            'Data Real': data_ant.strftime('%d/%m/%Y'), 'Data': 'Saldo Anterior', 'Data_dt': data_ant,
            'Histórico': 'SALDO ANTERIOR HERDADO', 'Débito': saldo_ant, 'Crédito': Decimal('0.00')
        })

    for idx, row in df_alvo.iterrows():
        dt_str = row['Data'].strftime('%d/%m/%Y')
        valor = Decimal(str(row['Valor']))
        if row[col_deb] == conta_alvo:
            todos_debitos.append({'Data Real': dt_str, 'Data': dt_str, 'Data_dt': row['Data'], 'Histórico': row['Histórico'], 'Débito': valor, 'Crédito': Decimal('0.00')})
        if row[col_cred] == conta_alvo:
            todos_creditos.append({'Data Real': dt_str, 'Data': dt_str, 'Data_dt': row['Data'], 'Histórico': row['Histórico'], 'Débito': Decimal('0.00'), 'Crédito': valor})

    todos_debitos.sort(key=lambda x: x['Data_dt'])
    todos_creditos.sort(key=lambda x: x['Data_dt'])

    # ETAPA 1: fecha o saldo anterior (se houver) contra uma combinação de créditos
    indices_credito_ant = []
    if tem_saldo_anterior:
        indices_credito_ant = _fechar_saldo_anterior(saldo_ant, todos_creditos)
    saldo_anterior_fechado = tem_saldo_anterior and len(indices_credito_ant) > 0

    debitos_pool_idx = list(range(1, len(todos_debitos))) if saldo_anterior_fechado else list(range(len(todos_debitos)))
    creditos_pool_idx = [i for i in range(len(todos_creditos)) if i not in set(indices_credito_ant)]

    # ETAPA 2: matching direto valor-a-valor (1 débito = 1 crédito), respeitando a janela
    debitos_usados, creditos_usados = _matching_direto(debitos_pool_idx, creditos_pool_idx, todos_debitos, todos_creditos, janela_dias)
    debitos_restantes = [i for i in debitos_pool_idx if i not in debitos_usados]
    creditos_restantes = [i for i in creditos_pool_idx if i not in creditos_usados]

    # ETAPA 3: matching em grupo por NF-e (usa identificador exato quando existe)
    if usar_matching_nfe:
        d_nfe, c_nfe = _matching_por_nfe(
            debitos_restantes, creditos_restantes, todos_debitos, todos_creditos,
            janela_dias=janela_dias, max_parcelas=max_parcelas_nfe
        )
        debitos_usados |= d_nfe
        creditos_usados |= c_nfe
        debitos_restantes = [i for i in debitos_restantes if i not in d_nfe]
        creditos_restantes = [i for i in creditos_restantes if i not in c_nfe]

    # ETAPA 4: matching em grupo por janela (sem identificador - ex: cartão: venda + taxa + recebimento)
    if usar_matching_janela:
        d_jan, c_jan = _matching_por_janela(
            debitos_restantes, creditos_restantes, todos_debitos, todos_creditos,
            janela_dias=janela_dias, max_grupo=max_grupo_janela
        )
        debitos_usados |= d_jan
        creditos_usados |= c_jan
        debitos_restantes = [i for i in debitos_restantes if i not in d_jan]
        creditos_restantes = [i for i in creditos_restantes if i not in c_jan]

    debitos_pend_idx = debitos_restantes
    creditos_pend_idx = creditos_restantes

    eventos_ant = []
    if saldo_anterior_fechado:
        eventos_ant = [todos_debitos[0]] + [todos_creditos[i] for i in indices_credito_ant]

    eventos_atual = [todos_debitos[i] for i in debitos_usados] + [todos_creditos[i] for i in creditos_usados]
    eventos_pend = [todos_debitos[i] for i in debitos_pend_idx] + [todos_creditos[i] for i in creditos_pend_idx]

    chave_ordem = lambda x: (x['Data_dt'], x['Crédito'] > 0)
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

st.markdown("---")
arquivo_lancamentos = st.file_uploader("📁 Anexe a Base Geral de Lançamentos (.xls ou .xlsx)", type=["xls", "xlsx"])

empresa, periodo, contas_nomes = None, None, {}
if arquivo_lancamentos:
    empresa, periodo, contas_nomes = extrair_cabecalho(arquivo_lancamentos.getvalue())

if empresa:
    st.markdown(f"### 🏢 {empresa}")
if periodo:
    st.caption(f"🗓️ Competência/Período: {periodo}")

with st.sidebar:
    st.subheader("⚙️ Configurações de Matching")
    janela_dias = st.number_input("📅 Janela máxima entre débito e crédito (dias)", value=60, step=10, min_value=1)
    st.caption("Crédito nunca pode ser anterior ao débito. Fora dessa janela, o item fica pendente.")
    st.markdown("---")
    usar_nfe = st.checkbox("Agrupar por NF-e (nota com CFOP dividido)", value=True)
    max_parcelas = st.number_input("Máx. de parcelas por grupo NF-e", value=6, step=1, min_value=1)
    st.markdown("---")
    usar_janela = st.checkbox("Agrupar por janela (cartões: máscara de bits)", value=True)
    st.caption("Para cartões, busca exata em centavos usando inteiros como máscaras de bits; fornecedores permanecem no fluxo sem o fallback de janela.")
    max_grupo_janela = st.number_input("Máx. de itens por grupo (janela)", value=6, step=1, min_value=1)

col_nat, col_conta = st.columns([1.3, 1])
with col_nat:
    modo = st.radio("Natureza da conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
with col_conta:
    conta_input = st.number_input("🔢 Digite a conta contábil alvo", value=0, step=1)
    if conta_input != 0 and conta_input in contas_nomes:
        st.caption(f"📄 {contas_nomes[conta_input]}")
    elif conta_input != 0 and arquivo_lancamentos:
        st.caption("⚠️ Conta não encontrada no arquivo anexado.")

st.markdown("---")
arquivo_balancete = st.file_uploader("📁 Anexe o Balancete Opcional (.xls ou .xlsx)", type=["xls", "xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de {formatar_brl(saldo_abertura_var)} capturado do Balancete.")
    except Exception as e:
        st.error(f"Erro ao analisar o Balancete: {e}")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Saldo Anterior Manual (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)

        tem_na_base = (df_base_geral['Débito'] == conta_input).any() or (df_base_geral['Crédito'] == conta_input).any()
        if not tem_na_base:
            st.error(f"❌ EXECUÇÃO BLOQUEADA: A conta {conta_input} não possui lançamentos no arquivo anexado. Digite o número correto.")
            st.stop()

        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'
        nome_conta = contas_nomes.get(conta_input, str(conta_input))

        r_tot, r_ant, r_atual, r_pend, s_tot, s_ant, s_atual, s_pend = processar_razoes_contabeis(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria,
            janela_dias=janela_dias, usar_matching_nfe=usar_nfe, usar_matching_janela=(usar_janela and tipo_auditoria == 'CARTAO'),
            max_parcelas_nfe=max_parcelas, max_grupo_janela=max_grupo_janela
        )

        if len(r_ant) == 0 and len(r_atual) == 0:
            st.error("🚨 ATENÇÃO: NENHUMA CONCILIAÇÃO OCORREU. Não foram encontrados pares/grupos que fechem na base enviada.")

        titulo_relatorio = f"Auditoria_{nome_conta.replace(' ', '_')}_{conta_input}"
        excel_data = gerar_excel_memoria({
            '1. Razão Total': r_tot,
            '2. Conciliado (Ano Anterior)': r_ant,
            '3. Conciliado (Atual)': r_atual,
            '4. Não Conciliado (Pendente)': r_pend
        })

        st.download_button(label="📥 Baixar 4 Razões (Excel)", data=excel_data,
                           file_name=f"{titulo_relatorio}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        st.write("---")
        if empresa:
            st.markdown(f"#### 📋 {empresa} — Conta {conta_input} ({nome_conta}){' — ' + periodo if periodo else ''}")
        st.subheader("Balanço de Validação")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("📘 Total", formatar_brl(s_tot))
        col2.metric("✅ Ano Anterior", formatar_brl(s_ant))
        col3.metric("✅ Atual", formatar_brl(s_atual))
        col4.metric("⚠️ Pendente", formatar_brl(s_pend))

        if abs(s_ant) == 0.0 and abs(s_atual) == 0.0 and round(s_tot, 2) == round(s_pend, 2):
            st.success("✅ **Auditoria Validada:** Os itens conciliados fecharam em R$ 0,00 perfeitamente.")

        st.markdown("---")
        tab1, tab2, tab3, tab4 = st.tabs([
            "📘 Total", "✅ Ano Anterior", "✅ Atual", "⚠️ Pendente"
        ])
        with tab1:
            st.dataframe(pd.DataFrame(r_tot), use_container_width=True, hide_index=True)
        with tab2:
            st.dataframe(pd.DataFrame(r_ant), use_container_width=True, hide_index=True)
        with tab3:
            st.dataframe(pd.DataFrame(r_atual), use_container_width=True, hide_index=True)
        with tab4:
            st.dataframe(pd.DataFrame(r_pend), use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
