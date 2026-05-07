import sqlite3
conn = sqlite3.connect('data/admission.db')
remaining = conn.execute("SELECT COUNT(*) FROM admission_process WHERE json_extract(attributes, '$.수능최저.있음') = 1 AND json_extract(attributes, '$.수능최저.조건') IS NULL").fetchone()[0]
total = conn.execute('SELECT COUNT(*) FROM admission_process').fetchone()[0]
print(f'Remaining unparsed: {remaining} / {total} = {remaining/total*100:.1f}%')
rows = conn.execute("SELECT d.university, COUNT(*) cnt FROM admission_process p JOIN admission_department d ON d.id = p.department_id WHERE json_extract(p.attributes, '$.수능최저.있음') = 1 AND json_extract(p.attributes, '$.수능최저.조건') IS NULL GROUP BY d.university ORDER BY cnt DESC LIMIT 10").fetchall()
print('Top universities still unparsed:')
for r in rows:
    print(f'  {r[0]}: {r[1]}')
