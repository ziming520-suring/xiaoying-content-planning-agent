"""Independent project-2 API budget. Unknown calls keep their full reserve."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from .catalog import ToolError, canonical, require

DEFAULT_BUDGET=Path(__file__).resolve().parents[2]/'var/bailian-budget.sqlite3'
RESERVE=100000


class BudgetLedger:
    def __init__(self,path=DEFAULT_BUDGET,*,limit_micro=10000000):
        self.path=Path(path)
        self.policy=(1,limit_micro,RESERVE,1,3)
        require(type(limit_micro) is int and 0 < limit_micro <= 10000000,'Invalid API budget','MODEL_CONFIG_ERROR')
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('CREATE TABLE IF NOT EXISTS policy (version INTEGER,limit_micro INTEGER,reserve_micro INTEGER,input_rate INTEGER,output_rate INTEGER)')
                c.execute('CREATE TABLE IF NOT EXISTS calls (call_key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,prompt_tokens INTEGER,completion_tokens INTEGER,response_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
                if not c.execute('SELECT 1 FROM policy').fetchone():
                    require(not c.execute('SELECT 1 FROM calls').fetchone(),'Missing existing policy','MODEL_BUDGET_ERROR')
                    c.execute('INSERT INTO policy VALUES (?,?,?,?,?)',self.policy)
                self._check(c)
                c.commit()
        except (sqlite3.Error,OSError) as exc:
            raise ToolError('MODEL_BUDGET_ERROR','Cannot open API budget ledger') from exc

    def connect(self):
        return closing(sqlite3.connect(self.path,timeout=5))

    def _check(self,c):
        require(c.execute('SELECT * FROM policy').fetchall()==[self.policy], 'Budget policy changed; no reset allowed','MODEL_BUDGET_ERROR')

    def _totals(self,c):
        rows=c.execute('SELECT prompt_tokens,completion_tokens FROM calls').fetchall()
        spent=held=prompt=completion=0
        for p,q in rows:
            if p is None and q is None:
                held+=RESERVE
            else:
                require(type(p) is int and type(q) is int and 0<=p<=65000 and 0<=q<=2048,'Invalid stored usage','MODEL_BUDGET_ERROR')
                prompt+=p; completion+=q; spent+=p+3*q
        return {'calls':len(rows),'settled_micro':spent,'held_micro':held,'accounted_micro':spent+held,
                'prompt_tokens':prompt,'completion_tokens':completion,'limit_micro':self.policy[1]}

    def reserve(self,key,request_hash):
        try:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE');self._check(c)
                row=c.execute('SELECT request_hash,response_json FROM calls WHERE call_key=?',(key,)).fetchone()
                if row:
                    require(row[0]==request_hash,'API call key parameter conflict','MODEL_CALL_CONFLICT')
                    require(row[1] is not None,'Previous API call outcome is unresolved; not resent','MODEL_PENDING')
                    return json.loads(row[1])
                totals=self._totals(c)
                require(totals['accounted_micro']+RESERVE<=self.policy[1],'API budget exhausted','MODEL_BUDGET_EXCEEDED')
                c.execute('INSERT INTO calls(call_key,request_hash) VALUES (?,?)',(key,request_hash))
                c.commit()
                return None
        except sqlite3.Error as exc:
            raise ToolError('MODEL_BUDGET_ERROR','Cannot reserve API budget; no send permitted') from exc

    def record(self,key,response):
        usage=response.get('usage',{}) if isinstance(response,dict) else {}
        p,q=(usage.get('prompt_tokens'),usage.get('completion_tokens')) if isinstance(usage,dict) else (None,None)
        require(type(p) is int and type(q) is int and 0<=p<=65000 and 0<=q<=2048,'Missing or invalid usage; reserve retained','MODEL_USAGE_ERROR')
        require(isinstance(response.get('id'),str) and response['id'],'Missing response identity','MODEL_USAGE_ERROR')
        payload=canonical(response)
        try:
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE');self._check(c)
                old=c.execute('SELECT prompt_tokens,completion_tokens,response_json FROM calls WHERE call_key=?',(key,)).fetchone()
                require(old is not None and (old==(None,None,None) or old==(p,q,payload)),'Conflicting API settlement','MODEL_BUDGET_ERROR')
                c.execute('UPDATE calls SET prompt_tokens=?,completion_tokens=?,response_json=? WHERE call_key=?',(p,q,payload,key))
                c.commit()
        except sqlite3.Error as exc:
            raise ToolError('MODEL_BUDGET_ERROR','Cannot persist API response; reserve retained') from exc

    def summary(self):
        with self.connect() as c:
            self._check(c)
            result=self._totals(c)
        result.update(accounted_yuan=f'{result["accounted_micro"]/1000000:.6f}',
                      remaining_yuan=f'{(result["limit_micro"]-result["accounted_micro"])/1000000:.6f}',
                      estimated_yuan=f'{(result["prompt_tokens"]*0.8+result["completion_tokens"]*2)/1000000:.6f}',
                      notice='Conservative accounting, not provider bill; unresolved calls retain 0.10 CNY')
        return result

    def outcome(self,key):
        with self.connect() as c:
            self._check(c)
            row=c.execute('SELECT response_json FROM calls WHERE call_key=?',(key,)).fetchone()
        return 'not_sent' if row is None else 'received' if row[0] is not None else 'unknown'
