Monitor de Boletos

Este projeto é um monitor automático de boletos bancários em PDF.
Ele observa a pasta de downloads, identifica boletos recém-salvos, extrai o nome do pagador e o número do documento, renomeia o arquivo e move para a pasta correspondente ao vendedor responsável.

O sistema possui:

Monitoramento em tempo real da pasta de downloads

Extração automática de dados (Banco do Brasil e Sicoob suportados)

Renomeação inteligente dos arquivos (ex.: PADARIA DO LUIS 4454.pdf)

Movimentação para pastas de vendedores configuráveis

Interface gráfica (Tkinter) para configurar pastas e vendedores

Ícone na bandeja do sistema para acesso rápido às configurações

⚙️ Requisitos

Python 3.9 ou superior

Pip instalado

Bibliotecas necessárias:
pip install pdfplumber pystray watchdog pillow


Tkinter já vem com a maioria das instalações do Python.

📥 Instalação

Clone o repositório:

git clone https://github.com/SEU_USUARIO/monitor_de_boletos_completo.git
cd monitor_de_boletos_completo


Instale as dependências:

pip install -r requirements.txt


(Se não houver requirements.txt, instale manualmente os pacotes listados acima.)

▶️ Uso

Execute o programa principal:

python monitor_boletos.py


Ao iniciar, ele ficará oculto e criará um ícone na bandeja do sistema.

Clique com o botão direito no ícone para acessar Configurações ou Fechar.

🛠️ Configuração

Na janela de Configurações você pode:

Definir a pasta monitorada (normalmente Downloads)

Adicionar vendedores, informando:

Nome do vendedor

Pasta de destino

Estado (opcional)

Palavras-chave para identificação automática

Os vendedores são salvos no arquivo config.json.

📂 Estrutura do Projeto
monitor_de_boletos_completo/
│── monitor_boletos.py   # Código principal
│── config.json          # Configurações salvas (criado automaticamente)
│── README.md            # Documentação

🚀 Futuras melhorias

Suporte a outros bancos

Logs exportáveis

Opção para rodar como serviço no Windows/Linux

👨‍💻 Autor

Projeto desenvolvido por Rodrigo Delgado.
