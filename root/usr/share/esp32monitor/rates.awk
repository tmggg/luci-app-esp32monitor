# Compare/subtract decimal strings before numeric conversion, preserving uint64
# increments even above 2^53 on both 32-bit and 64-bit awk implementations.
function normalize(s) { sub(/^0+/, "", s); return s == "" ? "0" : s }
function delta(a,b,    i,j,borrow,d,result) {
    a=normalize(a); b=normalize(b)
    if(length(b)<length(a) || (length(b)==length(a) && ("x" b)<("x" a))) return -1
    j=length(a); borrow=0; result=""
    for(i=length(b); i>0; i--) {
        d=substr(b,i,1)-(j>0 ? substr(a,j--,1) : 0)-borrow
        borrow=d<0; if(borrow) d+=10
        result=d result
    }
    return normalize(result)+0
}
function rate(old,current,dt,valid,    diff) {
    if(!valid || old=="null" || current=="null" || dt<=0) return "null"
    diff=delta(old,current)
    return diff<0 ? "null" : sprintf("%.0f",diff*1000/dt)
}
FILENAME==ARGV[1] { rx[$1]=$2; tx[$1]=$3; idx[$1]=$4; stamp[$1]=$5; next }
{
    dt=$5-stamp[$1]
    valid=($1 in idx) && $4!="null" && $4==idx[$1]
    down=rate(rx[$1],$2,dt,valid); up=rate(tx[$1],$3,dt,valid)
    if(count++) printf ","
    printf "{\"name\":\"%s\",\"ifindex\":%s,\"download_bps\":%s,\"upload_bps\":%s,\"download_bytes\":%s,\"upload_bytes\":%s,\"interval_ms\":%s}", $1,$4,down,up,$2,$3,(valid && dt>0 ? sprintf("%.0f",dt) : "null")
}
