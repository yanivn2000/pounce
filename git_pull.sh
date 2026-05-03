pkill -f streamlit
sleep 2
ps aux | grep streamlit
source venv/bin/activate
nohup streamlit run app.py --server.port=8501 --server.address=0.0.0.0 &
sleep 3
tail -20 nohup.out
