@echo off
set /p msg="Digite a mensagem do commit: "
git add .
git commit -m "%msg%"
git push origin main
echo ✅ Backup concluído com sucesso no GitHub!
pause   