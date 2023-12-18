import { spawn } from 'child_process';
import http from 'http';
import express from 'express';
import open from 'open';
import fs from 'fs';

let pythonServer;

process.on('SIGINT', () => {
  console.log('SIGINT received');
  pythonServer.kill('SIGINT');
  process.exit(0);
});

function checkServer(port) {
  return new Promise((resolve) => {
    const request = http.get(
      { hostname: 'localhost', port: port, path: '/' },
      (res) => {
        resolve(res.statusCode === 200);
      },
    );

    request.on('error', () => {
      resolve(false);
    });
  });
}

async function main() {
  const port = 5000; // replace with your Python server's port
  const isServerRunning = await checkServer(port);

  if (!isServerRunning) {
    pythonServer = spawn('python', ['./steno.py']);

    pythonServer.stdout.on('data', (data) => {
      console.log(`stdout: ${data}`);
    });

    pythonServer.stderr.on('data', (data) => {
      console.error(`stderr: ${data}`);
    });

    pythonServer.on('close', (code) => {
      console.log(`Python server exited with code ${code}`);
    });
  } else {
    console.log('Python server is already running');
  }

  // serve the ./index.html file on port 3000
  const app = express();

  app.use(express.static(fs.realpathSync('./')));

  app.listen(3000, () => {
    console.log('View frontend running on http://localhost:3000/index.html');
    open('http://localhost:3000/index.html');
  });

  // Run "npm run open-viewer" to open prisma studio
  const viewer = spawn('npm', ['run', 'open-viewer']);

  viewer.stdout.on('data', (data) => {
    console.log(`stdout: ${data}`);
  });

  viewer.stderr.on('data', (data) => {
    console.error(`stderr: ${data}`);
  });

  viewer.on('close', (code) => {
    console.log(`Prisma Studio exited with code ${code}`);
  });
}

main();
