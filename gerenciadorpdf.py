import os
import time
import re
import pdfplumber
import shutil
import json
import threading
import pystray
import tkinter as tk
from tkinter import messagebox, filedialog
from PIL import Image, ImageDraw
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import sys
from queue import Queue

# =========================================================================
# CLASSE DE GERENCIAMENTO DE CONFIGURAÇÕES
# Salva e carrega as configurações do arquivo 'config.json'
# =========================================================================
class ConfigManager:
    """Gerencia as configurações do software em um arquivo JSON."""
    
    def __init__(self, filename="config.json"):
        # **ALTERAÇÃO IMPORTANTE AQUI:**
        # Garante que o arquivo de configuração esteja SEMPRE na mesma pasta do executável
        if getattr(sys, 'frozen', False):
            # Se o script estiver empacotado (como .exe)
            base_dir = os.path.dirname(sys.executable)
        else:
            # Se for executado como script Python normal
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        self.filename = os.path.join(base_dir, filename)
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                print("❌ Erro ao ler o arquivo de configuração. Criando um novo.")
        
        return {
            "downloads_folder": None,
            "vendedores": []
        }

    def save_config(self):
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    def get_vendedores(self):
        return self.config["vendedores"]

    def set_vendedores(self, vendedores_list):
        self.config["vendedores"] = vendedores_list
        self.save_config()

# =========================================================================
# CLASSE DE PROCESSAMENTO DE BOLETOS
# Contém a lógica de extração, renomeação e movimentação
# =========================================================================
class BoletoProcessor:
    """
    Processa um arquivo PDF de boleto, extraindo dados, renomeando
    e movendo-o para a pasta correta do vendedor.
    """
    
    def __init__(self, config_manager, log_queue):
        self.config_manager = config_manager
        self.log_queue = log_queue
    
    def _print_to_gui(self, message):
        self.log_queue.put(message)

    def _limpar_nome(self, nome):
        """Remove caracteres inválidos para nomes de arquivos."""
        return re.sub(r'[\\/:"*?<>|]+', '', nome).strip()

    def _extrair_bb(self, texto):
        nome_pagador = None
        numero_doc = None

        # 1) Tenta extrair pelo rótulo 'Nome do Pagador' seguido do CNPJ
        try:
            m = re.search(r"Nome do Pagador[^\n]*\n\s*(?P<nome>.+?)\s+(?P<cnpj>\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
                          texto, re.IGNORECASE | re.DOTALL)
            if m:
                nome_pagador = m.group('nome').strip()
        except Exception:
            nome_pagador = None

        # 2) Se não encontrou pelo rótulo, procura qualquer CNPJ e pega a linha anterior
        if not nome_pagador:
            cnpj_pat = re.compile(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}')
            m = cnpj_pat.search(texto)
            if m:
                # pega o trecho até 200 caracteres antes do CNPJ e usa a última linha válida
                start = max(0, m.start() - 200)
                trecho = texto[start:m.start()]
                linhas = [l.strip() for l in trecho.splitlines() if l.strip()]
                if linhas:
                    candidato = linhas[-1]
                    # remove rótulos comuns se existirem
                    candidato = re.sub(r'^(Nome do Pagador[:\s-]*|Nome do Benef[ií]ciario[:\s-]*|Nome do Beneficiario[:\s-]*)', '', candidato, flags=re.IGNORECASE).strip()
                    # evita pegar palavras como 'CEP' ou 'RUA' como nome
                    if candidato and not re.search(r'CEP:|RUA|ENDERE', candidato, re.IGNORECASE):
                        nome_pagador = candidato

        # 3) Extrai número do documento (Nr. do Documento / Nr. do documento)
        try:
            mdoc = re.search(r'(Nr\. do Documento|Nr\. do documento|Nº Documento|N\. documento)[^\d\n]*\n?\s*([0-9\-]{4,})', texto, re.IGNORECASE)
            if mdoc:
                nums = re.findall(r'\d{4,}', mdoc.group(2))
                nums = [n for n in nums if not (1900 <= int(n) <= 2099)]
                if nums:
                    numero_doc = nums[0]
        except Exception:
            numero_doc = None

        # 4) fallback: procura por 'Nr. do Documento' em qualquer lugar e pega dígitos na próxima linha
        if not numero_doc:
            linhas = texto.split('\n')
            for i, linha in enumerate(linhas):
                if re.search(r'Nr\.? do Documento|Nr\.? do documento|Nº Documento|N\. documento', linha, re.IGNORECASE):
                    # tenta pegar na mesma linha
                    nums = re.findall(r'\d{4,}', linha)
                    nums = [n for n in nums if not (1900 <= int(n) <= 2099)]
                    if nums:
                        numero_doc = nums[0]
                        break
                    # tenta na próxima linha
                    if i+1 < len(linhas):
                        prox = linhas[i+1]
                        nums = re.findall(r'\d{4,}', prox)
                        nums = [n for n in nums if not (1900 <= int(n) <= 2099)]
                        if nums:
                            numero_doc = nums[0]
                            break

        return nome_pagador, numero_doc

    def _extrair_sicoob(self, texto):
        nome_pagador = None
        numero_doc = None
        linhas = texto.split('\n')
        for i, linha in enumerate(linhas):
            if linha.strip().startswith("Pagador:"):
                nome_pagador = linha.replace("Pagador:", "").strip()
                break
            elif linha.strip().startswith("Pagador Vencimento"):
                if i+1 < len(linhas):
                    nome_pagador = re.split(r"\d", linhas[i+1])[0].strip()
                    break
        for i, linha in enumerate(linhas):
            if "Nº Documento" in linha or "N. documento" in linha:
                if i+1 < len(linhas):
                    prox_linha = linhas[i+1]
                    numeros = [n for n in re.findall(r'\d{5,}', prox_linha)]
                    if numeros:
                        numero_doc = numeros[0]
                        break
                    numeros = [n for n in re.findall(r'\d{4,}', prox_linha) if '-' not in n]
                    if len(numeros) >= 2:
                        numero_doc = numeros[1]
                        break
                    elif len(numeros) == 1:
                        numero_doc = numeros[0]
                        break
        if not numero_doc:
            for linha in linhas:
                numeros = [n for n in re.findall(r'\d{4,}', linha) if '-' not in n]
                if len(numeros) >= 2:
                    numero_doc = numeros[1]
                    break
        return nome_pagador, numero_doc

    def extrair_dados_do_pdf(self, caminho_pdf):
        """Tenta extrair dados usando as funções de extração."""
        try:
            texto = ""
            with pdfplumber.open(caminho_pdf) as pdf:
                for pagina in pdf.pages:
                    texto += pagina.extract_text() + "\n"
            
            nome_pagador, numero_doc = self._extrair_bb(texto)
            if nome_pagador and numero_doc:
                return nome_pagador, numero_doc, texto

            nome_pagador, numero_doc = self._extrair_sicoob(texto)
            if nome_pagador and numero_doc:
                return nome_pagador, numero_doc, texto
            
            return None, None, None

        except Exception as e:
            self._print_to_gui(f"❌ Erro ao processar PDF: {e}")
            return None, None, None

    def _get_destino(self, nome_pagador_renomeado, texto_completo):
        """Identifica a pasta de destino com base nas configurações."""
        vendedores = self.config_manager.get_vendedores()
        candidatos = []
        
        for vendedor in vendedores:
            for palavra in vendedor.get("palavras_chave", []):
                if palavra.lower() in nome_pagador_renomeado.lower():
                    candidatos.append(vendedor)
                    break
        
        if len(candidatos) > 1:
            for vendedor in candidatos:
                estado_boleto = self._extrair_estado_do_texto(texto_completo)
                if estado_boleto and vendedor.get("estado") and estado_boleto.lower() == vendedor["estado"].lower():
                    return vendedor["pasta"]
            self._print_to_gui("⚠️ Múltiplos vendedores encontrados, mas não foi possível desambiguar pelo estado.")
            return None
        
        elif len(candidatos) == 1:
            return candidatos[0]["pasta"]
        
        self._print_to_gui("⚠️ Não foi encontrado nenhum vendedor para este boleto.")
        return None

    def _extrair_estado_do_texto(self, texto):
        siglas_estados = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO']
        for sigla in siglas_estados:
            if re.search(r'\b' + sigla + r'\b', texto, re.IGNORECASE):
                return sigla
        return None

    def processar_boleto(self, filepath):
        """Função principal para processar o boleto."""
        self._print_to_gui(f"\n📄 Novo boleto detectado: {filepath}")
        for _ in range(10):
            try:
                with open(filepath, "rb") as f: f.read(1)
                break
            except Exception:
                time.sleep(1)

        nome_pagador, numero_doc, texto_completo = self.extrair_dados_do_pdf(filepath)

        if nome_pagador and numero_doc:
            nome_limpo = self._limpar_nome(nome_pagador.upper())
            numero_doc_limpo = self._limpar_nome(numero_doc)
            novo_nome = f"{nome_limpo} {numero_doc_limpo}.pdf"
            
            caminho_antigo = filepath
            caminho_renomeado = os.path.join(os.path.dirname(caminho_antigo), novo_nome)
            try:
                os.rename(caminho_antigo, caminho_renomeado)
                self._print_to_gui(f"✅ Arquivo renomeado para: {novo_nome}")
            except Exception as e:
                self._print_to_gui(f"❌ Erro ao renomear o arquivo: {e}")
                return

            pasta_destino = self._get_destino(nome_limpo, texto_completo)
            
            if pasta_destino and os.path.isdir(pasta_destino):
                caminho_destino_completo = os.path.join(pasta_destino, novo_nome)
                try:
                    shutil.move(caminho_renomeado, caminho_destino_completo)
                    self._print_to_gui(f"✅ Arquivo movido para: {caminho_destino_completo}")
                except Exception as e:
                    self._print_to_gui(f"❌ Erro ao mover o arquivo para a pasta do vendedor: {e}")
            else:
                self._print_to_gui(f"❌ Pasta de destino não encontrada para este boleto.")
        else:
            self._print_to_gui("⚠️ Não foi possível extrair as informações necessárias para renomear.")

# =========================================================================
# CLASSES DE INTERFACE GRÁFICA (GUI)
# Inclui a janela de configurações e a janela de edição de vendedores
# =========================================================================
class ConfigGUI(tk.Toplevel):
    def __init__(self, parent, config_manager, observer, event_handler, log_queue):
        super().__init__(parent)
        self.config_manager = config_manager
        self.observer = observer
        self.event_handler = event_handler
        self.log_queue = log_queue
        self.vendedores = self.config_manager.get_vendedores()
        self.title("Configurações do Monitor de Boletos")
        self.geometry("800x700")

        self.downloads_path_var = tk.StringVar()
        saved_path = self.config_manager.get("downloads_folder")
        if not saved_path:
            saved_path = os.path.join(os.path.expanduser("~"), "Downloads")
        self.downloads_path_var.set(saved_path)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.create_widgets()
        self.after(100, self.update_log_from_queue)

    def on_close(self):
        self.withdraw()

    def create_widgets(self):
        main_frame = tk.Frame(self, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        downloads_frame = tk.LabelFrame(main_frame, text="Pasta Monitorada", padx=10, pady=10)
        downloads_frame.pack(fill=tk.X, pady=5)
        tk.Label(downloads_frame, text="Caminho:").pack(side=tk.LEFT, padx=(0, 5))
        tk.Entry(downloads_frame, textvariable=self.downloads_path_var, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(downloads_frame, text="Procurar...", command=self.browse_downloads_folder).pack(side=tk.LEFT)
        tk.Button(downloads_frame, text="Salvar", command=self.save_downloads_folder).pack(side=tk.LEFT)

        vendedores_frame = tk.LabelFrame(main_frame, text="Vendedores", padx=10, pady=10)
        vendedores_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.vendedores_listbox = tk.Listbox(vendedores_frame, height=15)
        self.vendedores_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        self.vendedores_listbox.bind("<Double-Button-1>", self.edit_vendedor)

        scrollbar = tk.Scrollbar(vendedores_frame, orient=tk.VERTICAL, command=self.vendedores_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.vendedores_listbox.config(yscrollcommand=scrollbar.set)
        
        button_frame = tk.Frame(vendedores_frame)
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)
        tk.Button(button_frame, text="Adicionar Vendedor", command=self.add_vendedor).pack(fill=tk.X, pady=5)
        tk.Button(button_frame, text="Editar Vendedor", command=self.edit_vendedor).pack(fill=tk.X, pady=5)
        tk.Button(button_frame, text="Remover Vendedor", command=self.remove_vendedor).pack(fill=tk.X, pady=5)

        self.update_vendedores_listbox()
        
        log_frame = tk.LabelFrame(main_frame, text="Log de Eventos", padx=10, pady=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = tk.Text(log_frame, state='disabled', wrap='word')
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def update_log_from_queue(self):
        while not self.log_queue.empty():
            message = self.log_queue.get()
            self.update_log(message)
        self.after(100, self.update_log_from_queue)

    def update_log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', message + '\n')
        self.log_text.configure(state='disabled')
        self.log_text.see('end')
    
    def browse_downloads_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.downloads_path_var.set(folder_selected)

    def save_downloads_folder(self):
        new_path = self.downloads_path_var.get()
        if os.path.isdir(new_path):
            old_path = self.config_manager.get("downloads_folder")
            self.config_manager.set("downloads_folder", new_path)
            
            if self.observer.is_alive():
                self.observer.stop()
                self.observer.join()
            
            self.observer = Observer()
            self.observer.schedule(self.event_handler, new_path, recursive=False)
            self.observer.start()
            
            messagebox.showinfo("Salvo", f"Caminho da pasta de downloads atualizado para '{new_path}' e monitoramento iniciado!")
            self.update_log(f"🔍 Monitorando a pasta: {new_path}")
        else:
            messagebox.showerror("Erro", "O caminho selecionado não é uma pasta válida.")
            self.update_log("❌ Erro: O caminho selecionado não é uma pasta válida.")

    def update_vendedores_listbox(self):
        self.vendedores_listbox.delete(0, tk.END)
        for vendedor in self.vendedores:
            self.vendedores_listbox.insert(tk.END, vendedor["nome"])
    
    def add_vendedor(self):
        self.open_vendedor_editor()

    def edit_vendedor(self, event=None):
        try:
            index = self.vendedores_listbox.curselection()[0]
            vendedor = self.vendedores[index]
            self.open_vendedor_editor(vendedor, index)
        except IndexError:
            messagebox.showwarning("Aviso", "Selecione um vendedor para editar.")

    def remove_vendedor(self):
        try:
            index = self.vendedores_listbox.curselection()[0]
            if messagebox.askyesno("Confirmar Remoção", "Tem certeza que deseja remover este vendedor?"):
                self.vendedores.pop(index)
                self.config_manager.set_vendedores(self.vendedores)
                self.update_vendedores_listbox()
        except IndexError:
            messagebox.showwarning("Aviso", "Selecione um vendedor para remover.")

    def open_vendedor_editor(self, vendedor=None, index=None):
        editor = VendedorEditor(self, vendedor)
        self.wait_window(editor)
        if editor.vendedor_data:
            if index is not None:
                self.vendedores[index] = editor.vendedor_data
            else:
                self.vendedores.append(editor.vendedor_data)
            self.config_manager.set_vendedores(self.vendedores)
            self.update_vendedores_listbox()

class VendedorEditor(tk.Toplevel):
    def __init__(self, parent, vendedor_data=None):
        super().__init__(parent)
        self.parent = parent
        self.vendedor_data = vendedor_data
        self.title("Editar Vendedor" if vendedor_data else "Adicionar Vendedor")
        # Aumenta tamanho inicial para evitar que o campo de entrada precise ser reposicionado
        self.geometry("700x520")
        # Define tamanho mínimo para evitar redimensionamento abaixo do aceitável
        self.minsize(600, 400)
        
        self.nome_var = tk.StringVar()
        self.pasta_var = tk.StringVar()
        self.estado_var = tk.StringVar()
        self.palavras_chave_list = []

        if self.vendedor_data:
            self.nome_var.set(self.vendedor_data.get("nome", ""))
            self.pasta_var.set(self.vendedor_data.get("pasta", ""))
            self.estado_var.set(self.vendedor_data.get("estado", ""))
            self.palavras_chave_list = self.vendedor_data.get("palavras_chave", [])
        
        self.create_widgets()

    def create_widgets(self):
        main_frame = tk.Frame(self, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(main_frame, text="Nome do Vendedor:").pack(anchor="w")
        tk.Entry(main_frame, textvariable=self.nome_var).pack(fill=tk.X)

        tk.Label(main_frame, text="Caminho da Pasta:").pack(anchor="w", pady=(10,0))
        tk.Entry(main_frame, textvariable=self.pasta_var).pack(fill=tk.X)
        tk.Button(main_frame, text="Procurar Pasta...", command=self.browse_folder).pack(fill=tk.X)

        tk.Label(main_frame, text="Estado (ex: MG, ES - opcional):").pack(anchor="w", pady=(10,0))
        tk.Entry(main_frame, textvariable=self.estado_var).pack(fill=tk.X)
        
        # Palavras-chave
        tk.Label(main_frame, text="Palavras-chave:").pack(anchor="w", pady=(10,0))
        self.palavras_chave_listbox = tk.Listbox(main_frame)
        self.palavras_chave_listbox.pack(fill=tk.BOTH, expand=True)
        self.update_palavras_chave_listbox()

        add_remove_frame = tk.Frame(main_frame)
        add_remove_frame.pack(fill=tk.X, pady=5)
        self.nova_palavra_entry = tk.Entry(add_remove_frame)
        self.nova_palavra_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(add_remove_frame, text="Adicionar", command=self.add_palavra).pack(side=tk.LEFT, padx=5)
        tk.Button(add_remove_frame, text="Remover", command=self.remove_palavra).pack(side=tk.LEFT)

        tk.Button(main_frame, text="Salvar", command=self.save_vendedor).pack(pady=10)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.pasta_var.set(folder_selected)

    def update_palavras_chave_listbox(self):
        self.palavras_chave_listbox.delete(0, tk.END)
        for palavra in self.palavras_chave_list:
            self.palavras_chave_listbox.insert(tk.END, palavra)
    
    def add_palavra(self):
        palavra = self.nova_palavra_entry.get().strip()
        if palavra and palavra not in self.palavras_chave_list:
            self.palavras_chave_list.append(palavra)
            self.update_palavras_chave_listbox()
            self.nova_palavra_entry.delete(0, tk.END)

    def remove_palavra(self):
        try:
            index = self.palavras_chave_listbox.curselection()[0]
            self.palavras_chave_list.pop(index)
            self.update_palavras_chave_listbox()
        except IndexError:
            pass

    def save_vendedor(self):
        nome = self.nome_var.get().strip()
        pasta = self.pasta_var.get().strip()
        estado = self.estado_var.get().strip()

        if not nome or not pasta:
            messagebox.showerror("Erro", "Nome e Pasta são obrigatórios!")
            return
        
        self.vendedor_data = {
            "nome": nome,
            "pasta": pasta,
            "estado": estado,
            "palavras_chave": self.palavras_chave_list
        }
        self.destroy()

# =========================================================================
# LÓGICA PRINCIPAL E MONITORAMENTO DE ARQUIVOS
# =========================================================================
class MonitorEventHandler(FileSystemEventHandler):
    """Lida com os eventos de criação e modificação de arquivos."""
    def __init__(self, boleto_processor):
        self.boleto_processor = boleto_processor
        
    def on_created(self, event):
        if not event.is_directory and ("boleto" in os.path.basename(event.src_path).lower()) and not event.src_path.endswith('.crdownload'):
            time.sleep(1) 
            threading.Thread(target=self.boleto_processor.processar_boleto, args=(event.src_path,)).start()

    def on_moved(self, event):
        if not event.is_directory and ("boleto" in os.path.basename(event.dest_path).lower()) and event.dest_path.endswith('.pdf'):
            threading.Thread(target=self.boleto_processor.processar_boleto, args=(event.dest_path,)).start()

# =========================================================================
# FUNÇÃO PARA CRIAR O ÍCONE NA BANDEJA DO SISTEMA
# =========================================================================
def create_systray_icon(config_window):
    """Cria o ícone na bandeja do sistema."""
    
    def open_settings(icon, item):
        config_window.deiconify()

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    image = Image.new('RGB', (64, 64), color=(255, 255, 255))
    d = ImageDraw.Draw(image)
    d.rectangle([16, 16, 48, 48], fill=(0, 120, 255))
    
    icon = pystray.Icon(
        "boleto_monitor",
        image,
        "Monitor de Boletos",
        menu=pystray.Menu(
            pystray.MenuItem('Configurar', open_settings),
            pystray.MenuItem('Fechar', on_quit)
        )
    )
    icon.run()

# =========================================================================
# PONTO DE ENTRADA DO PROGRAMA
# =========================================================================
if __name__ == "__main__":
    log_queue = Queue()
    
    config_manager = ConfigManager()
    boleto_processor = BoletoProcessor(config_manager, log_queue)
    
    event_handler = MonitorEventHandler(boleto_processor)
    observer = Observer()
    
    downloads_folder = config_manager.get("downloads_folder")
    if downloads_folder and os.path.isdir(downloads_folder):
        observer.schedule(event_handler, downloads_folder, recursive=False)
        observer.start()
        print(f"🔍 Monitorando a pasta: {downloads_folder}")
    else:
        print("▶️ Nenhum monitoramento ativo. Configure a pasta através do ícone na bandeja do sistema.")

    root = tk.Tk()
    root.withdraw()
    config_app = ConfigGUI(root, config_manager, observer, event_handler, log_queue)
    config_app.withdraw()
    
    tray_thread = threading.Thread(target=create_systray_icon, args=(config_app,))
    tray_thread.daemon = True
    tray_thread.start()

    root.mainloop()
