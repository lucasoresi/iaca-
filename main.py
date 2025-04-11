import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import openai
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Configuración OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")

# Configuración Supabase
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
)

# FastAPI
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prompt del sistema
system_prompt_sql = """
Sos un asistente experto que responde preguntas sobre una base de datos médica vinculada a prácticas, insumos, precios y usuarios.

Tenés acceso a una base de datos con múltiples tablas. Cada tabla contiene diferentes tipos de información y muchas están relacionadas entre sí a través de claves foráneas. A continuación, se detalla la estructura principal (simplificada) que debés tener en cuenta:

📦 Tablas y columnas importantes (ejemplos):

- `practices`: representa las prácticas médicas realizadas o disponibles. Contiene información como:
  - `id`: identificador único.
  - `name`: nombre de la práctica.
  - `code`: código interno o nomenclador.
  - `description`: descripción de la práctica.
  - `created_by` / `updated_by`: usuarios que crearon o modificaron la práctica.
  - `status`, `area`, `type`: categorizaciones específicas.

- `active_pricing`: precios asociados a prácticas por tipo (consumer, distribuitor, special), laboratorio, fecha de actualización, etc.
- `active_supplies`: información sobre insumos: código, nombre, clase, precio, proveedor, fechas.
- `availables_pricing`: prácticas disponibles con precio y contador de uso (`practice_count`).
- `jobs`: tareas o asignaciones relacionadas, con usuario (`user_id`), estado y fechas.
- `comments`: comentarios internos asociados a otras entidades (prácticas, insumos, etc.), con autor (`user_id`).
- `users`: contiene todos los usuarios del sistema (médicos, técnicos, administrativos, etc.).

🔗 Relaciones importantes:

- Muchas tablas tienen columnas como `created_by`, `updated_by` o `user_id` que hacen referencia a `users.id`.
- `practices` puede estar relacionada a precios, insumos y otras entidades según el contexto.
- Existen relaciones entre prácticas y otras entidades intermedias como `practices_in_practice` o tablas similares para organizar prácticas compuestas.

🎯 Instrucciones:

- Si una pregunta del usuario requiere datos concretos (nombres, cantidades, fechas, precios), primero generá una **consulta SQL válida en PostgreSQL**.
- No inventes datos. Si el dato está en la base, consultalo. Si no, explicá que no se puede obtener directamente.
- No expliques cómo funciona el SQL, solo mostrá el query y una respuesta tentativa.

El formato de tu respuesta debe ser:

📌 Ejemplos:

---
**Pregunta:** ¿Cuántas prácticas están asociadas al laboratorio IACA?
SQL: SELECT COUNT(*) FROM availables_pricing WHERE laboratory ILIKE 'IACA';

---
**Pregunta:** ¿Qué insumos se utilizan en la práctica con ID 5024?
SQL: SELECT s.name FROM supplies s JOIN practices_in_practice pip ON pip.supply_id = s.id WHERE pip.practice_id = 5024;

---
**Pregunta:** ¿Cuál es el precio actual para consumidor de la práctica “Hemograma completo”?
SQL: SELECT ap.consumer FROM active_pricing ap JOIN practices p ON p.code = ap.code WHERE p.name ILIKE 'Hemograma completo' ORDER BY ap.updated_at DESC LIMIT 1;

---
**Pregunta:** ¿Qué usuario modificó por última vez la práctica con código 1102?
SQL: SELECT u.name FROM practices p JOIN users u ON p.updated_by = u.id WHERE p.code = '1102' ORDER BY p.updated_at DESC LIMIT 1;

---

🛑 No inventes nombres de prácticas, insumos ni usuarios. Consultá directamente en la base usando SQL. Siempre priorizá precisión y claridad.
"""

@app.post("/chat")
async def chat(request: Request):
    body = await request.json()

    user_message = body.get("mensaje") or body.get("message")

    if not user_message or not isinstance(user_message, str):
        return {
            "error": "No se recibió un mensaje válido. Asegurate de enviar un campo 'mensaje' (string) en el body.",
            "message": None,
            "sql_query": None
        }

    try:
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt_sql},
                {"role": "user", "content": user_message}
            ],
            temperature=0,
        )
    except Exception as e:
        return {
            "error": f"Error al generar respuesta de OpenAI: {str(e)}",
            "message": None,
            "sql_query": None
        }

    response_text = completion.choices[0].message["content"].strip()
    print("🧠 Respuesta del modelo:", response_text)

    if response_text.startswith("SQL:"):
        try:
            sql_part = response_text.split("SQL:")[1].split("Respuesta:")[0].strip()

            if sql_part.endswith(";"):
                sql_part = sql_part[:-1].strip()

            print("🧠 SQL detectada:", sql_part)

            db_response = supabase.rpc("execute_sql", {"query": sql_part}).execute()

            if hasattr(db_response, "error") and db_response.error:
                return {
                    "error": str(db_response.error),
                    "sql_query": sql_part,
                    "message": "Ocurrió un error al ejecutar la consulta."
                }

            if db_response.data:
                data = db_response.data

                if all("name" in d and "price" in d and "description" in d for d in data):
                    respuesta = "Estos son los resultados:\n\n"
                    for item in data:
                        respuesta += f"- {item['name']}: {item['description']} (${item['price']})\n"
                elif all("name" in d for d in data):
                    nombres = ", ".join(d["name"] for d in data)
                    respuesta = f"Los resultados son: {nombres}."
                elif all("count" in d for d in data):
                    respuesta = f"Hay {data[0]['count']} elementos que cumplen con esa condición."
                else:
                    respuesta = f"Resultados obtenidos: {data}"

                return {
                    "message": respuesta,
                    "results": data,
                    "sql_query": sql_part
                }

            return {
                "message": "No se encontraron resultados.",
                "results": [],
                "sql_query": sql_part
            }

        except Exception as e:
            return {
                "error": f"Error al procesar la respuesta del modelo: {str(e)}",
                "raw_response": response_text
            }

    else:
        return {
            "message": response_text,
            "results": None,
            "sql_query": None
        }
