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

# ==========================================
# MÓDULOS DE CACHE E LEITURA
# ==========================================
@st.cache_data
def extrair_saldo_balancete(file_bytes, conta_alvo):
    df_balancete = pd.read_excel(io.BytesIO(file_bytes))
    cols_str = ' '.join(str(c).upper() for c in df_balancete.columns)
    
    if ('CÓDIGO' in cols_str or 'CODIGO' in cols_str or 'CONTA' in cols_str) and 'ANTERIOR' in cols_str:
        df_bal = df_balancete.copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.columns]
    else:
        header_idx = -1
        for idx, row in df_balancete.iterrows():
            row_str = ' '.join(str(x).upper() for x in row.values)
            if ('CÓDIGO' in row_str or 'CODIGO' in row_str or 'CONTA' in row_str) and 'ANTERIOR' in row_str:
                header_idx = idx
                break
        if header_idx == -1:
            raise ValueError("Não foi possível localizar as colunas de 'Código/Conta' e 'Saldo Anterior' no balancete.")
        df_bal = df_balancete.iloc[header_idx+1:].copy()
        df_bal.columns = [str(col).strip().upper() for col in df_balancete.iloc[header_idx].values]
    
    col_conta = next((c for c in df_bal.columns if 'CÓDIGO' in c or 'CODIGO' in c or 'CONTA' in c), None)
    col_saldo = next((c for c in df_bal.columns if 'ANTERIOR' in c), None)
    
    if not col_conta or not col_saldo:
        raise ValueError("O layout do Balancete não contém colunas claras.")
        
    df_bal[col_conta] = pd.to_numeric(df_bal[col_conta], errors='coerce')
    linha_conta = df_bal[df_bal[col_conta] == float(conta_alvo)]
    
    if linha_conta.empty: return Decimal('0.00')
    return formatar_moeda(linha_conta.iloc[0][col_saldo])

@st.cache_data
def carregar_base_lancamentos(file_bytes):
    df_raw = pd.read_excel(io.BytesIO(file_bytes), header=5)
    df_clean = df_raw.dropna(how='all', axis=1).dropna(how='all', axis=0).copy()
    headers = df_clean.iloc[0].tolist()
    
    new_headers = []
    counts = {}
    for h in headers:
        h_str = str(h) if pd.notna(h) else "Col_NaN"
        if h_str in counts:
            counts[h_str] += 1
            new_headers.append(f"{h_str}_{counts[h_str]}")
        else:
            counts[h_str] = 0
            new_headers.append(h_str)

    df_clean.columns = new_headers
    df_clean = df_clean.iloc[1:].reset_index(drop=True)
    df_main = df_clean[df_clean['Data'].notna()].copy()
    df_main = df_main[~df_main['Data'].astype(str).str.startswith('Conta:')].copy()
    
    df_main['Valor'] = df_main['Valor'].apply(formatar_moeda)
    df_main['Data'] = pd.to_datetime(df_main['Data'], errors='coerce')
    return df_main

# ==========================================
# MOTOR UNIVERSAL: LIQUIDAÇÃO EM BLOCO (NBC TG)
# ==========================================
def gerar_razao_e_blocos(df_main, conta_alvo, saldo_anterior_informado, tipo):
    if tipo == 'CARTAO':
        col_aumenta = 'Débito'
        col_diminui = 'Crédito'
        desc_aumenta = 'Venda Lançada'
        desc_diminui = 'Recebimento Bancário'
    else: # FORNECEDORES
        col_aumenta = 'Crédito'
        col_diminui = 'Débito'
        desc_aumenta = 'Nota Fiscal (Obrigação)'
        desc_diminui = 'Pagamento Realizado'

    df_alvo = df_main[(df_main['Débito'] == conta_alvo) | (df_main['Crédito'] == conta_alvo)].copy()
    saldo_ant = abs(Decimal(str(saldo_anterior_informado)))
    eventos = []

    # Injeta o Saldo Anterior
    if saldo_ant > Decimal('0.00'):
        data_base = df_alvo['Data'].min() if not df_alvo.empty else pd.to_datetime('2026-01-01')
        eventos.append({
            'Data Real': data_base - pd.Timedelta(seconds=1),
            'Data': 'Saldo Anterior',
            'Histórico': 'SALDO ANTERIOR HERDADO',
            'Aumenta Divida': saldo_ant,
            'Diminui Divida': Decimal('0.00'),
            'Tipo': 'Saldo Inicial'
        })

    # Extrai a movimentação
    for idx, row in df_alvo.iterrows():
        if row[col_aumenta] == conta_alvo:
            eventos.append({
                'Data Real': row['Data'], 'Data': row['Data'].strftime('%d/%m/%Y'), 'Histórico': row['Histórico'],
                'Aumenta Divida': Decimal(str(row['Valor'])), 'Diminui Divida': Decimal('0.00'), 'Tipo': desc_aumenta
            })
        if row[col_diminui] == conta_alvo:
            eventos.append({
                'Data Real': row['Data'], 'Data': row['Data'].strftime('%d/%m/%Y'), 'Histórico': row['Histórico'],
                'Aumenta Divida': Decimal('0.00'), 'Diminui Divida': Decimal(str(row['Valor'])), 'Tipo': desc_diminui
            })

    # Ordenação Cronológica: Para o mesmo dia, lança a dívida primeiro, depois o pagamento. Garante o fechamento perfeito do lote.
    eventos.sort(key=lambda x: (x['Data Real'], x['Aumenta Divida'] == Decimal('0.00')))

    razao_completo = []
    lotes_fechados = []
    pendencias_abertas = []

    saldo_acumulado = Decimal('0.00')
    lote_temporario = []
    id_lote = 1

    for ev in eventos:
        saldo_acumulado += ev['Aumenta Divida']
        saldo_acumulado -= ev['Diminui Divida']

        linha = {
            'Data': ev['Data'],
            'Histórico': ev['Histórico'],
            'Tipo': ev['Tipo'],
            f'Entrada (+ {desc_aumenta})': float(ev['Aumenta Divida']) if ev['Aumenta Divida'] > 0 else None,
            f'Saída (- {desc_diminui})': float(ev['Diminui Divida']) if ev['Diminui Divida'] > 0 else None,
            'Saldo Acumulado (Em Aberto)': float(saldo_acumulado)
        }
        razao_completo.append(linha)
        lote_temporario.append(linha)

        # O CÉREBRO DA AUDITORIA EM BLOCO: O Saldo zerou? Fecha o lote e isola.
        if saldo_acumulado == Decimal('0.00'):
            for item in lote_temporario:
                item['Lote de Conciliação'] = f'LOTE FECHADO-{id_lote:03d}'
                lotes_fechados.append(item)
            lote_temporario = []
            id_lote += 1

    # Tudo que não encontrou paridade exata para zerar vai para Pendências
    for item in lote_temporario:
        item['Lote de Conciliação'] = 'LOTE EM ABERTO (Pendência Atual)'
        pendencias_abertas.append(item)

    total_gerado = sum(e['Aumenta Divida'] for e in eventos if e['Tipo'] != 'Saldo Inicial')
    total_pago = sum(e['Diminui Divida'] for e in eventos)

    return razao_completo, lotes_fechados, pendencias_abertas, float(total_gerado), float(total_pago), float(saldo_acumulado)

def gerar_excel_memoria(dfs_dict):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        for sheet_name, data in dfs_dict.items():
            if data: pd.DataFrame(data).to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

# ==========================================
# INTERFACE DE USUÁRIO (STREAMLIT)
# ==========================================
st.title("Auditoria Contábil - Domínio Sistemas")
st.markdown("Liquidação Cronológica em Blocos (Regime NBC TG). Válido para Fornecedores e Cartões.")

modo = st.radio("Selecione a Natureza da Conta:", ["1. Cartões a Receber (Ativo)", "2. Fornecedores a Pagar (Passivo)"])
conta_input = st.number_input("Digite a conta contábil alvo (Ex: 623 ou 1059)", value=0, step=1)

st.markdown("---")
col_arq1, col_arq2 = st.columns(2)
with col_arq1:
    arquivo_lancamentos = st.file_uploader("1. Anexe a Base Geral de Lançamentos (.xlsx)", type=["xlsx"])
with col_arq2:
    arquivo_balancete = st.file_uploader("2. Anexe o Balancete Opcional (.xlsx)", type=["xlsx"])

saldo_abertura_var = Decimal('0.00')

if arquivo_balancete and conta_input != 0:
    try:
        bytes_balancete = arquivo_balancete.getvalue()
        saldo_abertura_var = extrair_saldo_balancete(bytes_balancete, conta_input)
        st.success(f"✔️ Saldo Anterior de R$ {float(saldo_abertura_var):,.2f} capturado do Balancete.".replace(",", "X").replace(".", ",").replace("X", "."))
    except Exception as e:
        st.error(f"Erro ao analisar o Balancete. Detalhe: {e}")
        saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00)))
elif not arquivo_balancete:
    saldo_abertura_var = Decimal(str(st.number_input("Digite o Saldo Anterior Manualmente (R$)", value=0.00, step=100.00)))

if arquivo_lancamentos and conta_input != 0:
    try:
        st.info("Calculando lotes de fechamento cronológico...")
        bytes_lancamentos = arquivo_lancamentos.getvalue()
        df_base_geral = carregar_base_lancamentos(bytes_lancamentos)
        
        tipo_auditoria = 'CARTAO' if 'Cartões' in modo else 'FORNECEDOR'
        
        razao, lotes_ok, lotes_pendentes, t_aumento, t_diminui, saldo_final = gerar_razao_e_blocos(
            df_base_geral, conta_input, saldo_abertura_var, tipo_auditoria
        )
        
        excel_data = gerar_excel_memoria({
            'Lotes Conciliados (Fechados)': lotes_ok,
            'Lote em Aberto (Pendências)': lotes_pendentes,
            'Extrato Razão Completo': razao
        })
        
        st.download_button(label="📥 Baixar Relatório de Auditoria", data=excel_data, 
                           file_name=f"Auditoria_Blocos_{conta_input}.xlsx", 
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        
        st.write("---")
        st.subheader("Balanço Sintético do Período")
        lbl_aumento = "Novas Vendas Lançadas" if tipo_auditoria == 'CARTAO' else "Novas NFs Lançadas"
        lbl_diminui = "Recebimentos/Taxas" if tipo_auditoria == 'CARTAO' else "Pagamentos Realizados"
        
        st.write(f"**Total {lbl_aumento}:** R$ {t_aumento:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        st.write(f"**Total {lbl_diminui}:** R$ {t_diminui:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        st.warning(f"**Saldo Final em Aberto (A Lançar no Balancete):** R$ {saldo_final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        
    except Exception as e:
        st.error(f"Falha na execução. Detalhe técnico: {e}")
