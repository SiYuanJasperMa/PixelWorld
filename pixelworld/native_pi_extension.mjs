// Retain Pi's native schemas, descriptions, editing and image behavior.
// Only the OS operations are delegated to the same case-only boundary.
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const pkg = await import(pathToFileURL(process.env.PIXELWORLD_PI_MODULE).href);
const workspace = process.env.PIXELWORLD_NATIVE_WORKSPACE;
const runtime = process.env.PIXELWORLD_NATIVE_PYTHON_RUNTIME;
const driver = process.env.PIXELWORLD_NATIVE_SANDBOX;

function operation(kind, value, input, onData, signal) {
  return new Promise((resolve, reject) => {
    const p = spawn(runtime+'/bin/python', [driver,'--workspace',workspace,'--runtime',runtime,kind,value],
      {env:{PATH:'/usr/bin:/bin'},stdio:['pipe','pipe','pipe'],detached:true});
    const out=[], err=[];
    p.stdout.on('data', b => {if(onData)onData(b);else out.push(b)});
    p.stderr.on('data', b => {if(onData)onData(b);else err.push(b)});
    const abort=()=>{try{process.kill(-p.pid,'SIGTERM')}catch{}};
    signal?.addEventListener('abort',abort,{once:true});
    p.on('error',reject);
    p.on('close',(code, sig)=>{
      signal?.removeEventListener('abort',abort);
      if(kind==='bash')resolve({exitCode:code ?? 128});
      else if(code===0)resolve(Buffer.concat(out));
      else reject(new Error(Buffer.concat(err).toString() || 'Case-only operation denied'));
    });
    p.stdin.on('error',()=>{});
    p.stdin.end(input ?? '');
  });
}

const readFile=path=>operation('read',path);
const access=async path=>{await operation('access',path)};
const writeFile=async(path,content)=>{await operation('write',path,content)};
const mkdir=async path=>{await operation('mkdir',path)};
const detectImageMimeType=async path=>{
  const b=await operation('head',path);
  if(b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return 'image/png';
  if(b[0]===255&&b[1]===216)return 'image/jpeg';
  if(b.subarray(0,3).toString()==='GIF')return 'image/gif';
  if(b.subarray(0,4).toString()==='RIFF'&&b.subarray(8,12).toString()==='WEBP')return 'image/webp';
  return undefined;
};

export default function(pi) {
  pi.registerTool(pkg.createReadToolDefinition(workspace,{
    autoResizeImages:false,operations:{readFile,access,detectImageMimeType}}));
  pi.registerTool(pkg.createWriteToolDefinition(workspace,{operations:{writeFile,mkdir}}));
  pi.registerTool(pkg.createEditToolDefinition(workspace,{operations:{readFile,writeFile,access}}));
  pi.registerTool(pkg.createBashToolDefinition(workspace,{
    exposeSessionEnvironment:false,
    operations:{exec:(command,cwd,options)=>operation('bash',command,undefined,options.onData,options.signal)}}));
}
