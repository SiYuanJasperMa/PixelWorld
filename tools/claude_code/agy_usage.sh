#!/bin/bash
# Print Gemini-group weekly / 5h remaining % from agy's interactive /usage screen.
S=agyusage$$; D=$(mktemp -d)
tmux new-session -d -s $S -x 260 -y 60 -c "$D" "$HOME/.local/bin/agy"; sleep 8
tmux send-keys -t $S Enter; sleep 4          # trust temp folder
tmux send-keys -t $S Escape; sleep 1
tmux send-keys -t $S -l "/usage"; sleep 2; tmux send-keys -t $S Enter; sleep 6
tmux capture-pane -t $S -p -J | grep -o "[0-9.]*%" | head -2 | paste -sd' ' | awk '{print "gemini_weekly_remaining="$1" gemini_5h_remaining="$2}'
tmux kill-session -t $S; rm -rf "$D"
