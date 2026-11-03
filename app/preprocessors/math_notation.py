"""Lossless-source companion notation for a limited MathML/OMML subset, no solving."""
def linear_math(node):
    tag=node.tag.rsplit('}',1)[-1]
    children=list(node)
    def one(name):
        value=next((c for c in children if c.tag.rsplit('}',1)[-1]==name),None)
        if value is None:raise ValueError('Missing math element '+name)
        return linear_math(value)
    if tag in {'mi','mn','mo','mtext','t'}:return node.text or ''
    if tag.endswith('Pr'):return ''
    if tag in {'math','mrow','oMath','oMathPara','r','e','num','den','sup','sub','deg'}:return ''.join(linear_math(c) for c in children)
    if tag=='f':return '('+one('num')+')/('+one('den')+')'
    if tag in {'mfrac','msup','msub','msubsup','mroot'}:
        expected=3 if tag=='msubsup' else 2
        if len(children)!=expected:raise ValueError('Invalid math arity')
        a=[linear_math(c) for c in children]
        return {'mfrac':lambda:f'({a[0]})/({a[1]})','msup':lambda:f'({a[0]})^({a[1]})','msub':lambda:f'({a[0]})_({a[1]})','msubsup':lambda:f'({a[0]})_({a[1]})^({a[2]})','mroot':lambda:f'root({a[1]},{a[0]})'}[tag]()
    if tag in {'sSup','sSub','sSubSup'}:
        result='('+one('e')+')'
        if tag in {'sSub','sSubSup'}:result+='_('+one('sub')+')'
        if tag in {'sSup','sSubSup'}:result+='^('+one('sup')+')'
        return result
    if tag=='msqrt':return 'sqrt('+''.join(linear_math(c) for c in children)+')'
    if tag=='rad':
        degree=one('deg');return 'root('+degree+','+one('e')+')' if degree else 'sqrt('+one('e')+')'
    raise ValueError('Unsupported math node '+tag)
