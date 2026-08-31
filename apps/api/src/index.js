import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import http from 'http';
import { Server } from 'socket.io';
import violationsRouter from './routes/violations.js';

dotenv.config();

const app = express();
app.use(cors());
app.use(express.json());

const server = http.createServer(app);
const io = new Server(server, {
  cors: { origin: '*' }
});

app.set('io', io); // supaya route lain bisa akses io untuk kirim event

io.on('connection', (socket) => {
  console.log('Dashboard terhubung via WebSocket:', socket.id);
});

app.get('/', (req, res) => {
  res.send('API APD Dashboard aktif!');
});

app.use('/api/events', violationsRouter);

const PORT = process.env.PORT || 5000;
server.listen(PORT, () => {
  console.log(`Server jalan di http://localhost:${PORT}`);
});