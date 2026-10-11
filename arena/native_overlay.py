"""Bounded native HUD text from the model's public decision reason."""
import unicodedata


def _columns(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in ('W','F') else 1 for c in text)


def decision_lines(decision, game_s, width=110, max_lines=3):
    if decision is None:
        return ['LLM says: waiting for the first accepted decision...']
    when=max(0,int(decision['game_s']))
    age=max(0,int(game_s-decision['game_s']))
    heading='LLM says | accepted %02d:%02d | %ds ago:' % (when//60,when%60,age)
    reason=decision.get('reason')
    if not isinstance(reason,str):
        return [heading,'  (No text reason supplied by the model.)']
    text=' '.join(reason.split())
    text=''.join(c for c in text if not unicodedata.category(c).startswith('C'))
    if not text:return [heading,'  (No text reason supplied by the model.)']
    # Bound visual width for both English and CJK; full reply stays in the log.
    lines=[];line=''
    for word in text.split(' '):
        if line and _columns(line+' '+word)<=width:line+=' '+word;continue
        if line:lines.append(line);line=''
        for char in word:
            if line and _columns(line+char)>width:lines.append(line);line=''
            line+=char
    if line:lines.append(line)
    if len(lines)>max_lines:
        lines=lines[:max_lines]
        while _columns(lines[-1]+' [...]')>width:lines[-1]=lines[-1][:-1]
        lines[-1]+=' [...]'
    return [heading]+['  '+line for line in lines]
